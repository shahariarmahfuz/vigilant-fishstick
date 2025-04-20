import os
import uuid
import shutil
import subprocess # ffmpeg চালানোর জন্য
import glob # ফাইল খুঁজে বের করার জন্য
from flask import Flask, request, render_template, jsonify, send_from_directory, abort

app = Flask(__name__)

# --- কনফিগারেশন ---
TEMP_FOLDER = os.path.join(os.getcwd(), 'temp_uploads')
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'uploads')
SEGMENTS_FOLDER = os.path.join(UPLOAD_FOLDER, 'segments') # সেগমেন্টের জন্য নতুন ফোল্ডার
FINAL_FILENAME = "final_video.mp4" # মূল এসেম্বল করা ফাইলের নাম
SEGMENT_DURATION = 1200 # 20 মিনিট = 1200 সেকেন্ড

# ফোল্ডারগুলো তৈরি করা যদি না থাকে
os.makedirs(TEMP_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(SEGMENTS_FOLDER, exist_ok=True) # সেগমেন্ট ফোল্ডার তৈরি

# --- ফাইল একত্রিত করার ফাংশন (আগের মতোই) ---
def assemble_chunks(upload_id, total_chunks, final_filename):
    temp_dir = os.path.join(TEMP_FOLDER, upload_id)
    final_file_path = os.path.join(UPLOAD_FOLDER, final_filename)
    try:
        with open(final_file_path, 'wb') as final_file:
            for i in range(total_chunks):
                chunk_path = os.path.join(temp_dir, f"chunk_{i}")
                if not os.path.exists(chunk_path):
                    print(f"Error: Chunk {i} not found for upload {upload_id}")
                    if os.path.exists(final_file_path): os.remove(final_file_path)
                    return False
                with open(chunk_path, 'rb') as chunk_file:
                    final_file.write(chunk_file.read())
        shutil.rmtree(temp_dir)
        print(f"Successfully assembled {final_filename} from upload {upload_id}")
        return final_file_path # সফল হলে ফাইলের পাথ রিটার্ন করা
    except Exception as e:
        print(f"Error assembling file {upload_id}: {e}")
        if os.path.exists(final_file_path): os.remove(final_file_path)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None # ব্যর্থ হলে None রিটার্ন করা

# --- ভিডিও ভাগ করার ফাংশন ---
def split_video_into_segments(input_filepath, output_folder, segment_prefix="see", segment_time=1200):
    """
    ffmpeg ব্যবহার করে ভিডিওকে নির্দিষ্ট সময়ের খণ্ডে ভাগ করে।
    """
    # আউটপুট ফাইলের প্যাটার্ন, যেমন: /app/static/uploads/segments/see%d.mp4
    output_pattern = os.path.join(output_folder, f"{segment_prefix}%d.mp4")

    # আগের সেগমেন্ট ফাইলগুলো মুছে ফেলা (যদি একই নামে আবার আপলোড হয়)
    existing_segments = glob.glob(os.path.join(output_folder, f"{segment_prefix}*.mp4"))
    for f in existing_segments:
        try:
            os.remove(f)
            print(f"Removed existing segment: {f}")
        except OSError as e:
            print(f"Error removing file {f}: {e}")


    # ffmpeg কমান্ড তৈরি
    command = [
        'ffmpeg',
        '-i', input_filepath,      # ইনপুট ফাইল
        '-c', 'copy',              # রি-এনকোড না করে স্ট্রিম কপি করা (দ্রুত)
        '-map', '0',               # সব স্ট্রিম (ভিডিও, অডিও) ম্যাপ করা
        '-segment_time', str(segment_time), # সেগমেন্টের সময় (সেকেন্ডে)
        '-f', 'segment',           # সেগমেন্ট ফরম্যাট ব্যবহার করা
        '-reset_timestamps', '1',  # প্রতিটি সেগমেন্টের শুরুতে টাইমস্ট্যাম্প রিসেট করা
        '-segment_start_number', '1', # সেগমেন্টের নম্বর ১ থেকে শুরু করা
        output_pattern             # আউটপুট ফাইলের প্যাটার্ন
    ]

    print(f"Running ffmpeg command: {' '.join(command)}")

    try:
        # ffmpeg কমান্ড চালানো
        process = subprocess.run(command, check=True, capture_output=True, text=True)
        print("ffmpeg stdout:", process.stdout)
        print("ffmpeg stderr:", process.stderr) # ffmpeg প্রায়শই stderr এ তথ্য লগ করে
        print(f"Successfully split video into segments in {output_folder}")

        # তৈরি হওয়া সেগমেন্ট ফাইলগুলোর তালিকা তৈরি করা
        segment_files = sorted(glob.glob(os.path.join(output_folder, f"{segment_prefix}*.mp4")))
        segment_urls = [f"/segments/{os.path.basename(f)}" for f in segment_files]
        print(f"Generated segments: {segment_urls}")
        return segment_urls # সেগমেন্টগুলোর URL লিস্ট রিটার্ন করা

    except subprocess.CalledProcessError as e:
        print(f"ffmpeg failed with error code {e.returncode}")
        print("ffmpeg stdout:", e.stdout)
        print("ffmpeg stderr:", e.stderr)
        return None # ব্যর্থ হলে None রিটার্ন করা
    except Exception as e:
        print(f"An error occurred during video splitting: {e}")
        return None # ব্যর্থ হলে None রিটার্ন করা

# --- রুট (Routes) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload_chunk', methods=['POST'])
def upload_chunk():
    try:
        file_chunk = request.files['file']
        chunk_index = int(request.form['chunkIndex'])
        total_chunks = int(request.form['totalChunks'])
        upload_id = request.form['uploadId']
        temp_dir = os.path.join(TEMP_FOLDER, upload_id)
        os.makedirs(temp_dir, exist_ok=True)
        chunk_path = os.path.join(temp_dir, f"chunk_{chunk_index}")
        file_chunk.save(chunk_path)

        print(f"Received chunk {chunk_index + 1}/{total_chunks} for upload {upload_id}")

        if chunk_index == total_chunks - 1:
            print(f"Last chunk received for {upload_id}. Assembling file...")
            assembled_filepath = assemble_chunks(upload_id, total_chunks, FINAL_FILENAME)

            if assembled_filepath:
                print(f"File assembled at: {assembled_filepath}. Starting segmentation...")
                # ভিডিও ভাগ করা শুরু করা
                segment_urls = split_video_into_segments(
                    input_filepath=assembled_filepath,
                    output_folder=SEGMENTS_FOLDER,
                    segment_prefix="see", # ফাইলের নামের শুরু 'see' দিয়ে হবে
                    segment_time=SEGMENT_DURATION
                )

                # ঐচ্ছিক: মূল এসেম্বল করা ফাইলটি মুছে ফেলা যদি প্রয়োজন না হয়
                # try:
                #     os.remove(assembled_filepath)
                #     print(f"Removed original assembled file: {assembled_filepath}")
                # except OSError as e:
                #     print(f"Could not remove original file {assembled_filepath}: {e}")


                if segment_urls:
                    return jsonify({
                        "message": "File assembled and split successfully!",
                        "segment_urls": segment_urls # ক্লায়েন্টকে সেগমেন্ট URL গুলোর লিস্ট পাঠানো
                    })
                else:
                    # এসেম্বল সফল কিন্তু ভাগ করা ব্যর্থ হলে
                    return jsonify({"error": "File assembled but failed to split into segments."}), 500
            else:
                # এসেম্বল ব্যর্থ হলে
                return jsonify({"error": "Failed to assemble file chunks."}), 500

        return jsonify({"message": f"Chunk {chunk_index + 1} received."})

    except KeyError as e:
        print(f"Missing form data: {e}")
        return jsonify({"error": f"Missing data in request: {e}"}), 400
    except Exception as e:
        print(f"Error uploading chunk: {e}")
        return jsonify({"error": "An error occurred during chunk upload."}), 500

@app.route('/segments/<filename>')
def serve_segment(filename):
    """
    তৈরি হওয়া ভিডিও সেগমেন্ট ফাইলগুলো পরিবেশন করে।
    """
    # নিরাপত্তা: এখানে ফাইলের নাম ভ্যালিডেট করা অত্যন্ত জরুরি
    # যেমন: filename শুধু 'see' দিয়ে শুরু এবং '.mp4' দিয়ে শেষ হচ্ছে কিনা চেক করা
    if not filename.startswith("see") or not filename.endswith(".mp4"):
         abort(404, description="Invalid segment filename format.")

    # ফাইলের পাথ সঠিকভাবে তৈরি করা
    safe_path = os.path.normpath(os.path.join(SEGMENTS_FOLDER, filename))

    # নিশ্চিত করা যে পাথটি SEGMENTS_FOLDER এর ভিতরেই আছে
    if not safe_path.startswith(os.path.normpath(SEGMENTS_FOLDER) + os.sep):
         abort(403) # Forbidden access

    if not os.path.exists(safe_path):
        abort(404, description="Segment not found.")

    return send_from_directory(SEGMENTS_FOLDER, filename, as_attachment=False)

# /see.mp4 রুটটি আর প্রয়োজন নেই, কারণ আমরা এখন সেগমেন্ট ব্যবহার করছি।
# যদি মূল ফাইলটি রাখতে চান এবং দেখাতে চান, তবে নিচের রুটটি রাখতে পারেন:
# @app.route('/see.mp4')
# def serve_final_video():
#     """ নির্দিষ্ট মূল ফাইলটি পরিবেশন করে """
#     if not os.path.exists(os.path.join(UPLOAD_FOLDER, FINAL_FILENAME)):
#          abort(404, description="Original file not found.")
#     return send_from_directory(UPLOAD_FOLDER, FINAL_FILENAME, as_attachment=False)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000) # Docker এ চালানোর সময় host='0.0.0.0' জরুরি
