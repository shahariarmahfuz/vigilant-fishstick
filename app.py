import os
import uuid
import shutil
import subprocess
import glob
import threading # থ্রেডিং ইম্পোর্ট করা হলো
import time # স্ট্যাটাস ট্র্যাকিং এর জন্য
from flask import Flask, request, render_template, jsonify, send_from_directory, abort
import logging

app = Flask(__name__)

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- কনফিগারেশন ---
TEMP_FOLDER = os.path.join(os.getcwd(), 'temp_uploads')
UPLOAD_FOLDER_BASE = os.path.join(os.getcwd(), 'static', 'uploads')
SEGMENT_DURATION = 1200 # 20 মিনিট = 1200 সেকেন্ড

# --- স্ট্যাটাস ট্র্যাকিং এর জন্য গ্লোবাল ডিকশনারি ---
# প্রোডাকশনের জন্য এটি যথেষ্ট নয়, ডেটাবেস বা Redis ব্যবহার করা ভালো
# এছাড়াও মাল্টি-ওয়ার্কার পরিবেশে এটি কাজ করবে না, কারণ স্ট্যাটাস শুধু একটি ওয়ার্কারে থাকবে
# থ্রেড-সেফটির জন্য Lock ব্যবহার করা হচ্ছে
upload_status = {}
status_lock = threading.Lock()

# ফোল্ডার তৈরি
os.makedirs(TEMP_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_BASE, exist_ok=True)

# --- Helper Function to Validate UUID ---
def is_valid_uuid(val):
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False

# --- ফাইল একত্রিত করার ফাংশন (আগের মতোই) ---
def assemble_chunks(upload_id, video_id, total_chunks):
    temp_dir = os.path.join(TEMP_FOLDER, upload_id)
    video_upload_folder = os.path.join(UPLOAD_FOLDER_BASE, video_id)
    os.makedirs(video_upload_folder, exist_ok=True)
    final_file_path = os.path.join(video_upload_folder, "final_video.mp4")
    logging.info(f"[{video_id}] Assembling file to: {final_file_path}")
    try:
        if os.path.exists(final_file_path):
             os.remove(final_file_path)
        with open(final_file_path, 'wb') as final_file:
            for i in range(total_chunks):
                chunk_path = os.path.join(temp_dir, f"chunk_{i}")
                if not os.path.exists(chunk_path):
                    logging.error(f"[{video_id}] Error: Chunk {i} not found for upload {upload_id}")
                    if os.path.exists(final_file_path): os.remove(final_file_path)
                    return None
                with open(chunk_path, 'rb') as chunk_file:
                    final_file.write(chunk_file.read())
        shutil.rmtree(temp_dir)
        logging.info(f"[{video_id}] Successfully assembled {final_file_path}")
        return final_file_path
    except Exception as e:
        logging.error(f"[{video_id}] Error assembling file from {upload_id}: {e}", exc_info=True)
        if os.path.exists(final_file_path): os.remove(final_file_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None

# --- ভিডিও ভাগ করার ফাংশন (ব্যাকগ্রাউন্ড থ্রেডে চলবে) ---
def split_video_into_segments_background(video_id, input_filepath, segment_time=1200):
    """
    ffmpeg দিয়ে ভিডিও ভাগ করে এবং গ্লোবাল স্ট্যাটাস আপডেট করে।
    """
    global upload_status
    segment_prefix = "see"
    output_folder = os.path.join(UPLOAD_FOLDER_BASE, video_id, 'segments')
    os.makedirs(output_folder, exist_ok=True)
    output_pattern = os.path.join(output_folder, f"{segment_prefix}%d.mp4")

    # স্ট্যাটাস 'processing' সেট করা
    with status_lock:
        upload_status[video_id] = {"status": "processing", "message": "Splitting video..."}

    logging.info(f"[{video_id}] Background Task: Splitting video {input_filepath} into segments.")

    # পুরনো সেগমেন্ট মুছে ফেলা (যদি থাকে)
    existing_segments = glob.glob(os.path.join(output_folder, f"{segment_prefix}*.mp4"))
    for f in existing_segments:
        try: os.remove(f)
        except OSError as e: logging.error(f"[{video_id}] Error removing existing segment {f}: {e}")

    command = [
        'ffmpeg', '-i', input_filepath, '-c', 'copy', '-map', '0',
        '-segment_time', str(segment_time), '-f', 'segment',
        '-reset_timestamps', '1', '-segment_start_number', '1', output_pattern
    ]

    try:
        process = subprocess.run(command, check=True, capture_output=True, text=True, timeout=3600) # ১ ঘন্টা টাইমআউট
        logging.info(f"[{video_id}] Background Task: ffmpeg completed successfully.")
        logging.debug(f"[{video_id}] ffmpeg stdout:\n{process.stdout}")
        logging.debug(f"[{video_id}] ffmpeg stderr:\n{process.stderr}")

        # সেগমেন্ট URL তৈরি করা
        segment_files_paths = sorted(glob.glob(os.path.join(output_folder, f"{segment_prefix}*.mp4")))
        segment_urls = []
        for f_path in segment_files_paths:
            filename = os.path.basename(f_path)
            try:
                segment_number = int(filename.replace(segment_prefix, '').replace('.mp4', ''))
                url = f"/{video_id}/{segment_prefix}{segment_number}.mp4"
                segment_urls.append(url)
            except ValueError:
                 logging.warning(f"[{video_id}] Could not parse segment number from filename: {filename}")

        # স্ট্যাটাস 'completed' সেট করা
        with status_lock:
            upload_status[video_id] = {
                "status": "completed",
                "message": "Video splitting complete.",
                "segment_urls": segment_urls
            }
        logging.info(f"[{video_id}] Background Task: Status updated to completed. Segments: {segment_urls}")

        # ঐচ্ছিক: মূল ফাইল মুছে ফেলা
        # try:
        #     os.remove(input_filepath)
        #     logging.info(f"[{video_id}] Removed original assembled file: {input_filepath}")
        # except OSError as e:
        #     logging.error(f"[{video_id}] Could not remove original file {input_filepath}: {e}")


    except subprocess.TimeoutExpired:
         logging.error(f"[{video_id}] Background Task: ffmpeg command timed out.")
         with status_lock:
             upload_status[video_id] = {"status": "failed", "message": "Processing timed out."}
    except subprocess.CalledProcessError as e:
        logging.error(f"[{video_id}] Background Task: ffmpeg failed (code {e.returncode}). stderr:\n{e.stderr}")
        with status_lock:
            upload_status[video_id] = {"status": "failed", "message": f"ffmpeg error (code {e.returncode})."}
    except Exception as e:
        logging.error(f"[{video_id}] Background Task: An error occurred during splitting: {e}", exc_info=True)
        with status_lock:
            upload_status[video_id] = {"status": "failed", "message": "An unexpected error occurred during processing."}


# --- রুট (Routes) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload_chunk', methods=['POST'])
def upload_chunk():
    global upload_status
    try:
        upload_id = request.form['uploadId']
        video_id = request.form['videoId']

        if not is_valid_uuid(video_id):
            return jsonify({"error": "Invalid videoId format."}), 400

        file_chunk = request.files['file']
        chunk_index = int(request.form['chunkIndex'])
        total_chunks = int(request.form['totalChunks'])

        temp_dir = os.path.join(TEMP_FOLDER, upload_id)
        os.makedirs(temp_dir, exist_ok=True)
        chunk_path = os.path.join(temp_dir, f"chunk_{chunk_index}")
        file_chunk.save(chunk_path)

        logging.info(f"[{video_id}] Received chunk {chunk_index + 1}/{total_chunks} for uploadId {upload_id}")

        # শেষ খণ্ড পাওয়ার পর এসেম্বল এবং ব্যাকগ্রাউন্ড প্রসেসিং শুরু
        if chunk_index == total_chunks - 1:
            logging.info(f"[{video_id}] Last chunk received. Assembling file...")
            assembled_filepath = assemble_chunks(upload_id, video_id, total_chunks)

            if assembled_filepath:
                logging.info(f"[{video_id}] File assembled at: {assembled_filepath}. Starting background segmentation task.")

                # --- ব্যাকগ্রাউন্ড থ্রেড শুরু করা ---
                thread = threading.Thread(
                    target=split_video_into_segments_background,
                    args=(video_id, assembled_filepath, SEGMENT_DURATION)
                )
                thread.daemon = True # মূল প্রসেস এক্সিট করলে থ্রেড বন্ধ হবে
                thread.start()
                # ---------------------------------

                # প্রাথমিক স্ট্যাটাস সেট করা
                with status_lock:
                    upload_status[video_id] = {"status": "processing", "message": "File assembled. Starting video split..."}

                # --- ক্লায়েন্টকে দ্রুত রেসপন্স পাঠানো ---
                status_check_url = f"/status/{video_id}"
                return jsonify({
                    "message": "Upload complete. Processing started in background.",
                    "status_url": status_check_url, # ক্লায়েন্ট এই URL পোল করবে
                    "video_id": video_id
                })
                # -------------------------------------

            else:
                with status_lock: # ব্যর্থতার স্ট্যাটাস সেট করা
                    upload_status[video_id] = {"status": "failed", "message": "Failed to assemble file chunks."}
                return jsonify({"error": f"Failed to assemble file chunks for {video_id}."}), 500

        # যদি শেষ খণ্ড না হয়
        return jsonify({"message": f"Chunk {chunk_index + 1} received for {video_id}."})

    except KeyError as e:
        logging.error(f"Missing form data: {e}")
        return jsonify({"error": f"Missing data in request: {e}"}), 400
    except Exception as e:
        logging.error(f"Error uploading chunk: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred during chunk upload."}), 500

# --- নতুন স্ট্যাটাস চেক এন্ডপয়েন্ট ---
@app.route('/status/<video_id>')
def get_status(video_id):
    global upload_status
    if not is_valid_uuid(video_id):
         abort(400, description="Invalid video ID format.")

    with status_lock:
        status_info = upload_status.get(video_id)

    if status_info:
        logging.debug(f"Status check for {video_id}: {status_info}")
        return jsonify(status_info)
    else:
        # যদি কোনো স্ট্যাটাস না পাওয়া যায় (সম্ভবত আপলোড শুরুই হয়নি বা ভুল আইডি)
        logging.warning(f"Status requested for unknown videoId: {video_id}")
        return jsonify({"status": "not_found", "message": "Status not found for this video ID."}), 404


# --- সেগমেন্ট পরিবেশনের রুট (আগের মতোই) ---
@app.route('/<video_id>/see<int:segment_number>.mp4')
def serve_segment_by_id(video_id, segment_number):
    logging.info(f"Request received for segment: videoId={video_id}, number={segment_number}")
    if not is_valid_uuid(video_id): abort(400, description="Invalid video ID format.")
    if segment_number < 1: abort(400, description="Segment number must be 1 or greater.")

    segment_filename = f"see{segment_number}.mp4"
    segment_folder_path = os.path.join(UPLOAD_FOLDER_BASE, video_id, 'segments')
    requested_path = os.path.normpath(os.path.join(segment_folder_path, segment_filename))

    if not requested_path.startswith(os.path.normpath(segment_folder_path) + os.sep):
        logging.error(f"Forbidden access attempt: videoId={video_id}, segment={segment_number}")
        abort(403)

    if not os.path.exists(requested_path):
        logging.warning(f"Segment not found: {requested_path}")
        abort(404, description="Segment not found.")

    logging.info(f"Serving segment: {requested_path}")
    return send_from_directory(segment_folder_path, segment_filename, as_attachment=False)


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
    
