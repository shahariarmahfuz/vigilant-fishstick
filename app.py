import os
import uuid
import shutil
import subprocess
import glob
from flask import Flask, request, render_template, jsonify, send_from_directory, abort
import logging # ভালো লগিং এর জন্য

app = Flask(__name__)

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- কনফিগারেশন ---
TEMP_FOLDER = os.path.join(os.getcwd(), 'temp_uploads')
UPLOAD_FOLDER_BASE = os.path.join(os.getcwd(), 'static', 'uploads') # মূল আপলোড ফোল্ডার
# FINAL_FILENAME = "final_video.mp4" # এটি আর গ্লোবালি দরকার নেই, videoId'র ভেতরে থাকবে
SEGMENT_DURATION = 1200 # 20 মিনিট = 1200 সেকেন্ড

# ফোল্ডার তৈরি (শুধুমাত্র বেস ফোল্ডার)
os.makedirs(TEMP_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER_BASE, exist_ok=True)

# --- Helper Function to Validate UUID ---
def is_valid_uuid(val):
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False

# --- ফাইল একত্রিত করার ফাংশন (আপডেট করা) ---
def assemble_chunks(upload_id, video_id, total_chunks):
    """
    অস্থায়ী ফোল্ডার থেকে সব খণ্ড একত্রিত করে videoId'র জন্য নির্দিষ্ট ফোল্ডারে চূড়ান্ত ফাইল তৈরি করে।
    """
    temp_dir = os.path.join(TEMP_FOLDER, upload_id)
    # videoId'র জন্য ডেডিকেটেড ফোল্ডার তৈরি করা
    video_upload_folder = os.path.join(UPLOAD_FOLDER_BASE, video_id)
    os.makedirs(video_upload_folder, exist_ok=True)
    final_file_path = os.path.join(video_upload_folder, "final_video.mp4") # নির্দিষ্ট নাম রাখা যেতে পারে

    logging.info(f"[{video_id}] Assembling file to: {final_file_path}")
    try:
        # যদি ফাইল আগে থেকেই থাকে (সম্ভাব্য ব্যর্থ চেষ্টার পর), মুছে ফেলা
        if os.path.exists(final_file_path):
            logging.warning(f"[{video_id}] Found existing file, removing: {final_file_path}")
            os.remove(final_file_path)

        with open(final_file_path, 'wb') as final_file:
            for i in range(total_chunks):
                chunk_path = os.path.join(temp_dir, f"chunk_{i}")
                if not os.path.exists(chunk_path):
                    logging.error(f"[{video_id}] Error: Chunk {i} not found for upload {upload_id}")
                    if os.path.exists(final_file_path): os.remove(final_file_path) # আংশিক ফাইল মোছা
                    return None # একত্রিতকরণ ব্যর্থ

                with open(chunk_path, 'rb') as chunk_file:
                    final_file.write(chunk_file.read())
        # সফল হলে অস্থায়ী চাঙ্ক ফোল্ডার মুছে ফেলা
        shutil.rmtree(temp_dir)
        logging.info(f"[{video_id}] Successfully assembled {final_file_path} from temp dir {temp_dir}")
        return final_file_path # সফল হলে ফাইলের পাথ রিটার্ন করা
    except Exception as e:
        logging.error(f"[{video_id}] Error assembling file from {upload_id}: {e}", exc_info=True)
        # সমস্যা হলে আংশিক ফাইল এবং চাঙ্ক ফোল্ডার মুছে ফেলা
        if os.path.exists(final_file_path): os.remove(final_file_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None # ব্যর্থ হলে None রিটার্ন করা

# --- ভিডিও ভাগ করার ফাংশন (আপডেট করা) ---
def split_video_into_segments(video_id, input_filepath, segment_time=1200):
    """
    ffmpeg ব্যবহার করে ভিডিওকে নির্দিষ্ট সময়ের খণ্ডে ভাগ করে videoId'র জন্য নির্দিষ্ট ফোল্ডারে রাখে।
    """
    # videoId'র জন্য ডেডিকেটেড সেগমেন্ট ফোল্ডার তৈরি করা
    output_folder = os.path.join(UPLOAD_FOLDER_BASE, video_id, 'segments')
    os.makedirs(output_folder, exist_ok=True)
    segment_prefix = "see" # ফাইলের নামের শুরু 'see' দিয়ে হবে
    output_pattern = os.path.join(output_folder, f"{segment_prefix}%d.mp4")

    logging.info(f"[{video_id}] Splitting video: {input_filepath} into segments in {output_folder}")

    # যদি এই ভিডিওর জন্য আগে সেগমেন্ট তৈরি হয়ে থাকে, মুছে ফেলা
    existing_segments = glob.glob(os.path.join(output_folder, f"{segment_prefix}*.mp4"))
    if existing_segments:
        logging.warning(f"[{video_id}] Found existing segments, removing them...")
        for f in existing_segments:
            try:
                os.remove(f)
            except OSError as e:
                logging.error(f"[{video_id}] Error removing existing segment {f}: {e}")

    # ffmpeg কমান্ড
    command = [
        'ffmpeg',
        '-i', input_filepath,
        '-c', 'copy',
        '-map', '0',
        '-segment_time', str(segment_time),
        '-f', 'segment',
        '-reset_timestamps', '1',
        '-segment_start_number', '1', # নম্বর ১ থেকে শুরু
        output_pattern
    ]

    logging.info(f"[{video_id}] Running ffmpeg command: {' '.join(command)}")

    try:
        process = subprocess.run(command, check=True, capture_output=True, text=True, timeout=1800) # ৩০ মিনিট টাইমআউট দিলাম
        logging.info(f"[{video_id}] ffmpeg stdout:\n{process.stdout}")
        logging.info(f"[{video_id}] ffmpeg stderr:\n{process.stderr}") # stderr এ বেশি তথ্য থাকে
        logging.info(f"[{video_id}] Successfully split video into segments.")

        # তৈরি হওয়া সেগমেন্ট ফাইলগুলোর তালিকা তৈরি করা (সম্পূর্ণ পাথ সহ)
        segment_files_paths = sorted(glob.glob(os.path.join(output_folder, f"{segment_prefix}*.mp4")))

        # URL তৈরি করা (যেমন: /9b1deb4d.../see1.mp4)
        segment_urls = []
        for f_path in segment_files_paths:
            filename = os.path.basename(f_path)
            # filename থেকে নম্বর বের করা (see1.mp4 -> 1)
            try:
                segment_number = int(filename.replace(segment_prefix, '').replace('.mp4', ''))
                url = f"/{video_id}/{segment_prefix}{segment_number}.mp4"
                segment_urls.append(url)
            except ValueError:
                 logging.warning(f"[{video_id}] Could not parse segment number from filename: {filename}")

        logging.info(f"[{video_id}] Generated segment URLs: {segment_urls}")
        return segment_urls

    except subprocess.TimeoutExpired:
         logging.error(f"[{video_id}] ffmpeg command timed out after 1800 seconds.")
         return None
    except subprocess.CalledProcessError as e:
        logging.error(f"[{video_id}] ffmpeg failed (code {e.returncode}):\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")
        return None
    except Exception as e:
        logging.error(f"[{video_id}] An error occurred during video splitting: {e}", exc_info=True)
        return None

# --- রুট (Routes) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload_chunk', methods=['POST'])
def upload_chunk():
    try:
        # videoId এবং uploadId ক্লায়েন্ট থেকে নেওয়া
        upload_id = request.form['uploadId']
        video_id = request.form['videoId']

        # videoId ভ্যালিড UUID কিনা চেক করা (নিরাপত্তা)
        if not is_valid_uuid(video_id):
            logging.warning(f"Invalid videoId received: {video_id}")
            return jsonify({"error": "Invalid videoId format."}), 400

        file_chunk = request.files['file']
        chunk_index = int(request.form['chunkIndex'])
        total_chunks = int(request.form['totalChunks'])

        temp_dir = os.path.join(TEMP_FOLDER, upload_id)
        os.makedirs(temp_dir, exist_ok=True)
        chunk_path = os.path.join(temp_dir, f"chunk_{chunk_index}")
        file_chunk.save(chunk_path)

        logging.info(f"[{video_id}] Received chunk {chunk_index + 1}/{total_chunks} for uploadId {upload_id}")

        if chunk_index == total_chunks - 1:
            logging.info(f"[{video_id}] Last chunk received. Assembling file...")
            # এসেম্বল করার সময় videoId পাস করা হচ্ছে
            assembled_filepath = assemble_chunks(upload_id, video_id, total_chunks)

            if assembled_filepath:
                logging.info(f"[{video_id}] File assembled at: {assembled_filepath}. Starting segmentation...")
                # ভিডিও ভাগ করার সময় videoId পাস করা হচ্ছে
                segment_urls = split_video_into_segments(
                    video_id=video_id,
                    input_filepath=assembled_filepath,
                    segment_time=SEGMENT_DURATION
                )

                # ঐচ্ছিক: এসেম্বল করা মূল ফাইল মুছে ফেলা
                # try:
                #     os.remove(assembled_filepath)
                #     logging.info(f"[{video_id}] Removed original assembled file: {assembled_filepath}")
                # except OSError as e:
                #     logging.error(f"[{video_id}] Could not remove original file {assembled_filepath}: {e}")

                if segment_urls is not None: # চেক করতে হবে None কিনা, খালি লিস্ট হলেও সফল ধরা যায়
                    return jsonify({
                        "message": "File assembled and split successfully!",
                        "segment_urls": segment_urls
                    })
                else:
                    return jsonify({"error": f"File assembled for {video_id} but failed to split into segments."}), 500
            else:
                return jsonify({"error": f"Failed to assemble file chunks for {video_id}."}), 500

        return jsonify({"message": f"Chunk {chunk_index + 1} received for {video_id}."})

    except KeyError as e:
        logging.error(f"Missing form data: {e}")
        return jsonify({"error": f"Missing data in request: {e}"}), 400
    except Exception as e:
        logging.error(f"Error uploading chunk: {e}", exc_info=True)
        return jsonify({"error": "An internal error occurred during chunk upload."}), 500

# --- নতুন রুটে সেগমেন্ট পরিবেশন ---
@app.route('/<video_id>/see<int:segment_number>.mp4')
def serve_segment_by_id(video_id, segment_number):
    """
    নির্দিষ্ট videoId এবং সেগমেন্ট নম্বরের ভিডিও ফাইল পরিবেশন করে।
    যেমন: /9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d/see1.mp4
    """
    logging.info(f"Request received for segment: videoId={video_id}, number={segment_number}")

    # videoId ভ্যালিডেট করা
    if not is_valid_uuid(video_id):
        logging.warning(f"Invalid videoId requested: {video_id}")
        abort(400, description="Invalid video ID format.")

    if segment_number < 1:
        abort(400, description="Segment number must be 1 or greater.")

    # সেগমেন্ট ফাইলের প্রত্যাশিত নাম তৈরি
    segment_filename = f"see{segment_number}.mp4"
    # videoId'র জন্য নির্দিষ্ট সেগমেন্ট ফোল্ডারের পাথ
    segment_folder_path = os.path.join(UPLOAD_FOLDER_BASE, video_id, 'segments')

    # নিরাপত্তা: ফাইল পাথ ভ্যালিডেশন (normpath ব্যবহার করে অ্যাটাক ঠেকানো)
    requested_path = os.path.normpath(os.path.join(segment_folder_path, segment_filename))

    # পাথটি কি আসলেই segment_folder_path এর ভেতরে?
    if not requested_path.startswith(os.path.normpath(segment_folder_path) + os.sep):
        logging.error(f"Forbidden access attempt: videoId={video_id}, segment={segment_number}, requested_path={requested_path}")
        abort(403) # Forbidden

    # ফাইল আছে কিনা চেক করা
    if not os.path.exists(requested_path):
        logging.warning(f"Segment not found: {requested_path}")
        abort(404, description="Segment not found.")

    logging.info(f"Serving segment: {requested_path}")
    # send_from_directory ব্যবহার করে ফাইল পাঠানো
    # এখানে directory আর্গুমেন্ট হিসেবে segment_folder_path এবং filename হিসেবে segment_filename দিতে হবে
    return send_from_directory(segment_folder_path, segment_filename, as_attachment=False)


if __name__ == '__main__':
    # হোস্ট 0.0.0.0 Docker কন্টেইনারের জন্য জরুরি
    # debug=False প্রোডাকশনের জন্য ভালো, তবে ডেভেলপমেন্টের সময় True রাখতে পারেন
    app.run(debug=False, host='0.0.0.0', port=5000)
    
