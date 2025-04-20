# Base image হিসেবে Python 3.9-slim ব্যবহার করা হচ্ছে
FROM python:3.9-slim

# সিস্টেম প্যাকেজ আপডেট এবং ffmpeg ইনস্টল করা
# --no-install-recommends অপ্রয়োজনীয় প্যাকেজ ইনস্টল এড়াতে সাহায্য করে
# ইনস্টলেশনের পর apt cache পরিষ্কার করে ইমেজের সাইজ কমানো হচ্ছে
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# অ্যাপ্লিকেশন কোডের জন্য ওয়ার্কিং ডিরেক্টরি সেট করা
WORKDIR /app

# requirements.txt কপি করা এবং লাইব্রেরি ইনস্টল করা
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# অ্যাপ্লিকেশন কোড এবং টেমপ্লেট/স্ট্যাটিক ফাইল কপি করা
COPY . .

# আপলোড এবং সেগমেন্টের জন্য ডিরেক্টরি তৈরি করা (যদি রানটাইমে তৈরি না হয়)
# সাধারণত Python এর os.makedirs এটি হ্যান্ডেল করে, তবে নিশ্চিত হওয়ার জন্য যোগ করা যেতে পারে
RUN mkdir -p /app/temp_uploads /app/static/uploads/segments

# Flask অ্যাপ্লিকেশন যে পোর্টে চলবে সেটি এক্সপোজ করা
EXPOSE 5000

# কন্টেইনার চালু হলে অ্যাপ্লিকেশন রান করার কমান্ড
# Production-এর জন্য ['gunicorn', '-w', '4', '-b', '0.0.0.0:5000', 'app:app'] ব্যবহার করা ভালো
CMD ["python", "app.py"]
