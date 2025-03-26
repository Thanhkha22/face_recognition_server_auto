import os
import sqlite3
import datetime
import json
import io
import cv2
import numpy as np
import face_recognition
import requests
import uuid
import threading
import time
from flask import Flask, request, jsonify, render_template, send_from_directory, Response, redirect, url_for, flash, send_file
from flask_mail import Mail, Message
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from PIL import Image

# Khởi tạo ứng dụng Flask
app = Flask(__name__)
app.secret_key = '6112004'  # Khóa bí mật để bảo mật session

# **Cấu hình gửi email**
app.config['MAIL_SERVER'] = 'smtp.gmail.com'  # Máy chủ SMTP của Gmail
app.config['MAIL_PORT'] = 587  # Cổng SMTP
app.config['MAIL_USE_TLS'] = True  # Sử dụng TLS để mã hóa
app.config['MAIL_USERNAME'] = 'thanhkhazyd598@gmail.com'  # Email gửi
app.config['MAIL_PASSWORD'] = 'jhke mhgs dcup esxp'  # Mật khẩu ứng dụng (App Password) của Gmail
app.config['MAIL_DEFAULT_SENDER'] = 'thanhkhazyd598@gmail.com'  # Người gửi mặc định

# **Cấu hình Flask-Login để quản lý đăng nhập**
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # Chuyển hướng đến trang login nếu chưa đăng nhập

# Khởi tạo đối tượng gửi email
mail = Mail(app)

# **Định nghĩa thư mục và cơ sở dữ liệu**
UPLOAD_FOLDER = "uploads"  # Thư mục lưu ảnh khuôn mặt
DB_NAME = "events.db"  # Tên file cơ sở dữ liệu SQLite

# Tạo thư mục uploads nếu chưa tồn tại
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# **Khởi tạo cơ sở dữ liệu SQLite**
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Tạo bảng events để lưu sự kiện vào/ra
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            recognized_faces TEXT,
            count INTEGER,
            event_type TEXT
        )
    ''')
    # Tạo bảng faces để lưu thông tin khuôn mặt
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            face_id TEXT UNIQUE,
            name TEXT,
            dob TEXT,
            gender TEXT,
            timestamp TEXT
        )
    ''')
    # Tạo bảng users để lưu thông tin người dùng
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT
        )
    ''')
    conn.commit()
    conn.close()

# **Tạo tài khoản admin mặc định nếu chưa có**
def create_default_admin():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username=?", ("admin",))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                       ("admin", "123@abc", "admin"))
        conn.commit()
        print("Tài khoản admin đã được tạo!")
    else:
        print("Tài khoản admin đã tồn tại!")
    conn.close()

# Gọi hàm khởi tạo
init_db()
create_default_admin()

# **Định nghĩa lớp User cho Flask-Login**
class User(UserMixin):
    def __init__(self, id, username, password, role):
        self.id = id
        self.username = username
        self.password = password
        self.role = role

# **Lấy thông tin người dùng theo ID**
def get_user_by_id(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password, role FROM users WHERE id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return User(*row) if row else None

# **Lấy thông tin người dùng theo username**
def get_user_by_username(username):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password, role FROM users WHERE username=?", (username,))
    row = cursor.fetchone()
    conn.close()
    return User(*row) if row else None

# **Tải thông tin người dùng cho Flask-Login**
@login_manager.user_loader
def load_user(user_id):
    return get_user_by_id(user_id)

# **Tải danh sách khuôn mặt đã biết từ thư mục uploads**
def load_known_faces():
    known_face_encodings = []
    known_face_names = []
    for file in os.listdir(UPLOAD_FOLDER):
        if not file.lower().endswith((".jpg", ".png")):
            continue
        file_path = os.path.join(UPLOAD_FOLDER, file)
        face_id = file.split("_", 1)[0]
        try:
            image = face_recognition.load_image_file(file_path)
            encodings = face_recognition.face_encodings(image)
            if encodings:
                known_face_encodings.append(encodings[0])
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM faces WHERE face_id=?", (face_id,))
                row = cursor.fetchone()
                conn.close()
                known_face_names.append(row[0] if row and row[0] else "Unknown")
        except Exception as e:
            print(f"Lỗi khi tải ảnh {file}: {e}")
    print(f"Loaded {len(known_face_encodings)} known faces: {known_face_names}")
    return known_face_encodings, known_face_names

# Tải khuôn mặt đã biết khi khởi động ứng dụng
known_face_encodings, known_face_names = load_known_faces()

# **Các Route của ứng dụng Flask**

# Xóa một sự kiện khỏi cơ sở dữ liệu dựa trên ID
@app.route('/delete_event/<int:event_id>', methods=['DELETE'])
def delete_event(event_id):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()
        conn.close()
        return jsonify({"message": "Xóa sự kiện thành công!"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Tải xuống hóa đơn dưới dạng file văn bản
@app.route('/download_invoice', methods=['GET'])
@login_required
def download_invoice():
    invoice_data = {"name": "Người dùng A", "entry": "2025-02-27 14:43:00", "exit": "2025-02-27 14:45:00", "fee": "10000"}
    content = f"Hóa đơn cho: {invoice_data['name']}\nCheck-in: {invoice_data['entry']}\nCheck-out: {invoice_data['exit']}\nPhí phiên: {invoice_data['fee']} VNĐ\n"
    filename = "invoice.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    return send_file(filename, as_attachment=True)

# Tính phí sử dụng dựa trên thời gian vào/ra
@app.route('/calculate_fee', methods=['GET'])
@login_required
def calculate_fee():
    fee_rate = 5000 / 720  # VNĐ mỗi phút (5000 VNĐ / 12 giờ)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp, recognized_faces, event_type FROM events WHERE event_type IN ('Vào', 'Ra') ORDER BY timestamp ASC")
    rows = cursor.fetchall()
    conn.close()

    fees = {}
    for timestamp_str, faces_json, event_type in rows:
        try:
            face_data = json.loads(faces_json)
            face_id = face_data.get("face_id")
            name = face_data.get("name", "Unknown")
            timestamp_dt = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            if face_id not in fees:
                fees[face_id] = {"name": name, "sessions": [], "current_entry": None, "total_fee": 0}
            if event_type == "Vào":
                fees[face_id]["current_entry"] = timestamp_dt
            elif event_type == "Ra" and fees[face_id]["current_entry"]:
                entry_time = fees[face_id]["current_entry"]
                exit_time = timestamp_dt
                duration_minutes = (exit_time - entry_time).total_seconds() / 60
                session_fee = round(duration_minutes * fee_rate, 2)
                fees[face_id]["sessions"].append({
                    "entry": entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "exit": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "fee": session_fee
                })
                fees[face_id]["total_fee"] += session_fee
                fees[face_id]["current_entry"] = None
        except Exception:
            continue

    for data in fees.values():
        data["total_fee"] = round(data["total_fee"], 2)
        if "current_entry" in data:
            del data["current_entry"]
    return jsonify(fees)

# Nhận ảnh từ client, nhận diện khuôn mặt và trả về thông tin
@app.route('/upload', methods=['POST'])
def upload():
    try:
        image_data = request.data
        img = Image.open(io.BytesIO(image_data)).convert("RGB")
        img_np = np.array(img, dtype=np.uint8)
        face_locations = face_recognition.face_locations(img_np, model="hog")
        if not face_locations:
            return jsonify({"error": "Không phát hiện khuôn mặt"}), 400

        top, right, bottom, left = face_locations[0]
        margin_h, margin_w = int((bottom - top) * 0.2), int((right - left) * 0.2)
        top, bottom = max(0, top - margin_h), min(img_np.shape[0], bottom + margin_h)
        left, right = max(0, left - margin_w), min(img_np.shape[1], right + margin_w)
        face_img = img_np[top:bottom, left:right]

        encodings = face_recognition.face_encodings(face_img)
        if not encodings:
            return jsonify({"error": "Không thể lấy thông tin khuôn mặt"}), 400
        new_face_encoding = encodings[0]

        for file in os.listdir(UPLOAD_FOLDER):
            file_path = os.path.join(UPLOAD_FOLDER, file)
            try:
                existing_image = face_recognition.load_image_file(file_path)
                existing_encodings = face_recognition.face_encodings(existing_image)
                if existing_encodings and face_recognition.compare_faces([existing_encodings[0]], new_face_encoding, tolerance=0.4)[0]:
                    matched_face_id = file.split("_", 1)[0]
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("SELECT name, dob, gender FROM faces WHERE face_id=?", (matched_face_id,))
                    row = cursor.fetchone()
                    conn.close()
                    name, dob, gender = row if row else ("", "", "")
                    face_url = url_for('uploaded_file', filename=os.path.basename(file_path))
                    return jsonify({
                        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
                        "face_image": face_url,
                        "face_id": matched_face_id,
                        "name": name,
                        "dob": dob,
                        "gender": gender
                    })
            except Exception:
                continue

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        face_id = str(uuid.uuid4())
        face_filename = os.path.join(UPLOAD_FOLDER, f"{face_id}_{timestamp}.jpg")
        Image.fromarray(face_img).save(face_filename)
        face_url = url_for('uploaded_file', filename=os.path.basename(face_filename))
        return jsonify({"timestamp": timestamp, "face_image": face_url, "face_id": face_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



# Lưu thông tin khuôn mặt và sự kiện vào cơ sở dữ liệu
@app.route('/save_info', methods=['POST'])
def save_info():
    data = request.json
    face_id = data.get("face_id")
    name = data.get("name", "")
    dob = data.get("dob", "")
    gender = data.get("gender", "")
    face_image = data.get("face_image")
    event_type = data.get("event_type")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not face_id or not event_type:
        return jsonify({"error": "Thiếu dữ liệu cần thiết!"}), 400

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    face_info = {
        "face_id": face_id,
        "name": name,
        "dob": dob,
        "gender": gender,
        "face_image": face_image or ""
    }

    try:
        # Kiểm tra xem face_id đã có trong bảng faces chưa
        cursor.execute("SELECT face_id FROM faces WHERE face_id=?", (face_id,))
        row = cursor.fetchone()

        if row is None:
            # Chưa tồn tại -> Thêm mới
            cursor.execute("""
                INSERT INTO faces (face_id, name, dob, gender, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (face_id, name, dob, gender, timestamp))
        else:
            # Đã tồn tại -> Cập nhật thông tin (name, dob, gender, v.v.)
            cursor.execute("""
                UPDATE faces
                SET name = ?, dob = ?, gender = ?, timestamp = ?
                WHERE face_id = ?
            """, (name, dob, gender, timestamp, face_id))

        # Luôn ghi nhận sự kiện vào bảng events
        cursor.execute("""
            INSERT INTO events (timestamp, recognized_faces, count, event_type)
            VALUES (?, ?, ?, ?)
        """, (timestamp, json.dumps(face_info), 1, event_type))

        conn.commit()
        return jsonify({"message": f"Ghi nhận sự kiện {event_type} thành công!"}), 200

    except sqlite3.IntegrityError:
        return jsonify({"error": "Mã khuôn mặt đã tồn tại!"}), 400
    finally:
        conn.close()

# Lấy thông tin hiện tại (số người trong khu vực, lịch sử 5 sự kiện gần nhất)
@app.route('/get_current_info', methods=['GET'])
def get_current_info():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM events WHERE event_type = 'Vào'")
    count_in = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM events WHERE event_type = 'Ra'")
    count_out = cursor.fetchone()[0]
    cursor.execute("SELECT timestamp, recognized_faces, event_type FROM events ORDER BY timestamp DESC LIMIT 5")
    rows = cursor.fetchall()
    conn.close()

    history = [{"timestamp": row[0], "recognized_faces": json.loads(row[1]), "event_type": row[2]} for row in rows]
    last_update = history[0]["timestamp"] if history else "Chưa có dữ liệu"
    return jsonify({"current_count": count_in - count_out, "last_update": last_update, "history": history})

# Gửi email hỗ trợ từ người dùng tới danh sách email cố định
RECIPIENTS = ["khapt.22th@sv.dla.edu.vn", "phucnt.22th@sv.dla.edu.vn"]
@app.route("/send_support_email", methods=["POST"])
def send_support_email():
    data = request.json
    name = data.get("name")
    email = data.get("email")
    message = data.get("message")
    if not name or not email or not message:
        return jsonify({"error": "Thiếu thông tin"}), 400
    subject = f"Yêu cầu hỗ trợ từ {name}"
    body = f"Tên: {name}\nEmail: {email}\n\nNội dung:\n{message}"
    try:
        msg = Message(subject, recipients=RECIPIENTS, body=body)
        mail.send(msg)
        return jsonify({"message": "Email đã được gửi thành công!"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Xử lý đăng nhập người dùng
# Thêm cấu hình reCAPTCHA vào Flask
RECAPTCHA_SECRET_KEY = "6LehHgArAAAAAKVMLd2oOOLGnDMckjLefZau6pvN"

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        recaptcha_response = request.form.get('g-recaptcha-response')

        # Xác thực reCAPTCHA
        recaptcha_verify_url = "https://www.google.com/recaptcha/api/siteverify"
        recaptcha_data = {'secret': RECAPTCHA_SECRET_KEY, 'response': recaptcha_response}
        recaptcha_result = requests.post(recaptcha_verify_url, data=recaptcha_data).json()

        if not recaptcha_result.get('success'):
            flash("reCAPTCHA không hợp lệ. Vui lòng thử lại!", "danger")
            return redirect(url_for('login'))

        # Kiểm tra tài khoản trong cơ sở dữ liệu
        user = get_user_by_username(username)
        if user and user.password == password:
            login_user(user)
            flash("Đăng nhập thành công!", "success")
            return redirect(url_for('index'))

        flash("Tên đăng nhập hoặc mật khẩu không đúng!", "danger")

    return render_template('login.html')

# Xử lý tạo tài khoản mới
@app.route('/create_account', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        if not username or not password or not confirm_password:
            flash("Vui lòng điền đầy đủ thông tin!")
            return render_template('create_account.html')
        if password != confirm_password:
            flash("Mật khẩu xác nhận không khớp!")
            return render_template('create_account.html')
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                           (username, password, 'user'))
            conn.commit()
            flash("Tạo tài khoản thành công! Vui lòng đăng nhập.", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Tên đăng nhập đã tồn tại!")
        finally:
            conn.close()
    return render_template('create_account.html')

# Đăng xuất người dùng
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# Trang chính của ứng dụng
@app.route('/')
@login_required
def index():
    return render_template('index.html')

# Trang quản lý tài khoản (chỉ dành cho admin)
@app.route('/account_management')
@login_required
def account_management():
    if current_user.role != "admin":
        flash("Bạn không có quyền truy cập trang này!")
        return redirect(url_for('index'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password, role FROM users")
    rows = cursor.fetchall()
    conn.close()
    users = [{"id": row[0], "username": row[1], "password": row[2], "role": row[3]} for row in rows]
    return render_template('account_management.html', users=users)

# Thêm tài khoản mới (chỉ admin)
@app.route('/add_account', methods=['POST'])
@login_required
def add_account():
    if current_user.role != "admin":
        flash("Bạn không có quyền thực hiện thao tác này!")
        return redirect(url_for('index'))
    username = request.form.get('username')
    password = request.form.get('password')
    role = request.form.get('role')
    if not username or not password or not role:
        flash("Vui lòng điền đầy đủ thông tin!")
        return redirect(url_for('account_management'))
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                       (username, password, role))
        conn.commit()
        flash("Thêm tài khoản thành công!", "success")
    except sqlite3.IntegrityError:
        flash("Tên đăng nhập đã tồn tại!")
    finally:
        conn.close()
    return redirect(url_for('account_management'))

# Chỉnh sửa thông tin tài khoản (chỉ admin)
@app.route('/edit_account/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_account(user_id):
    if current_user.role != "admin":
        flash("Bạn không có quyền thực hiện thao tác này!")
        return redirect(url_for('index'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        if not username or not password or not role:
            flash("Vui lòng điền đầy đủ thông tin!")
            return redirect(url_for('edit_account', user_id=user_id))
        try:
            cursor.execute("UPDATE users SET username=?, password=?, role=? WHERE id=?",
                           (username, password, role, user_id))
            conn.commit()
            flash("Cập nhật tài khoản thành công!", "success")
        except sqlite3.IntegrityError:
            flash("Tên đăng nhập đã tồn tại!")
        finally:
            conn.close()
        return redirect(url_for('account_management'))
    cursor.execute("SELECT id, username, role FROM users WHERE id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        user = {"id": row[0], "username": row[1], "role": row[2]}
        return render_template('edit_account.html', user=user)
    flash("Không tìm thấy tài khoản!")
    return redirect(url_for('account_management'))

# Xóa tài khoản (chỉ admin)
@app.route('/delete_account/<int:user_id>')
@login_required
def delete_account(user_id):
    if current_user.role != "admin":
        flash("Bạn không có quyền thực hiện thao tác này!")
        return redirect(url_for('index'))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    flash("Xóa tài khoản thành công!", "success")
    return redirect(url_for('account_management'))

# Trang lịch sử sự kiện
@app.route('/history')
@login_required
def history():
    return render_template('history.html')

# Trang cài đặt
@app.route('/settings')
@login_required
def settings():
    return render_template('settings.html')

# Trang hỗ trợ
@app.route('/support')
@login_required
def support():
    return render_template('support.html')

# Trang quản lý khuôn mặt
@app.route('/face_management')
@login_required
def face_management():
    return render_template('face_management.html')

# Trang báo cáo
@app.route('/report')
@login_required
def report():
    return render_template('report.html')

# Phần chân trang
@app.route('/footer')
@login_required
def footer():
    return render_template('footer.html')

# Phần đầu trang
@app.route('/header')
@login_required
def header():
    return render_template('header.html')

# Phục vụ file từ thư mục models
@app.route('/models/<path:filename>')
def serve_models(filename):
    return send_from_directory('models', filename)

# Lấy số lượng sự kiện theo ngày cho báo cáo
@app.route('/report/daily_counts')
@login_required
def daily_counts():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT DATE(timestamp) AS day, COUNT(*) FROM events GROUP BY DATE(timestamp)")
    rows = cursor.fetchall()
    conn.close()
    data = [{"day": row[0], "count": row[1]} for row in rows]
    return jsonify(data)

# Lấy số lượng sự kiện vào/ra cho báo cáo
@app.route('/report/in_out_counts')
@login_required
def in_out_counts():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT event_type, COUNT(*) FROM events GROUP BY event_type")
    rows = cursor.fetchall()
    conn.close()
    data = {row[0]: row[1] for row in rows}
    return jsonify(data)

# Cập nhật thông tin sự kiện
@app.route('/update_event', methods=['POST'])
def update_event():
    data = request.json
    event_id = data.get('id')
    if not event_id:
        return jsonify({"error": "Thiếu ID sự kiện"}), 400
    recognized_faces = data.get('recognized_faces', {})
    event_type = data.get('event_type')
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE events SET recognized_faces=?, event_type=? WHERE id=?",
                   (json.dumps(recognized_faces), event_type, event_id))
    conn.commit()
    conn.close()
    return jsonify({"message": "Cập nhật thành công!"}), 200

# Lấy toàn bộ lịch sử sự kiện
@app.route('/get_history', methods=['GET'])
def get_history():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, timestamp, recognized_faces, count, event_type FROM events ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    conn.close()
    history = [{"id": row[0], "timestamp": row[1], "recognized_faces": json.loads(row[2]), "count": row[3], "event_type": row[4]} for row in rows]
    return jsonify(history)

# Phục vụ file ảnh từ thư mục uploads
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# Phục vụ favicon.ico
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

# Lấy danh sách khuôn mặt đã biết
@app.route('/get_known_faces', methods=['GET'])
def get_known_faces():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT face_id, name FROM faces")
    rows = cursor.fetchall()
    conn.close()
    known_faces = []
    for face_id, name in rows:
        for file in os.listdir(UPLOAD_FOLDER):
            if file.startswith(face_id + "_"):
                known_faces.append({
                    "face_id": face_id,
                    "name": name,
                    "image_url": url_for('uploaded_file', filename=file)
                })
                break
    return jsonify(known_faces)

# Phát video trực tiếp từ webcam với nhận diện khuôn mặt
@app.route('/video_feed')
def video_feed():
    def generate():
        cap = cv2.VideoCapture(1)
        if not cap.isOpened():
            print("Không mở được webcam!")
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb_frame, model="hog")
            face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
            for (top, right, bottom, left), face_encoding in zip(face_locations, face_encodings):
                distances = face_recognition.face_distance(known_face_encodings, face_encoding)
                if len(distances) > 0 and min(distances) < 0.25:
                    best_match_index = np.argmin(distances)
                    name = known_face_names[best_match_index]
                    color = (0, 255, 0)  # Xanh lá
                    cv2.putText(frame, name, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                else:
                    color = (0, 0, 255)  # Đỏ
                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            ret, jpeg = cv2.imencode('.jpg', frame)
            if ret:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
        cap.release()
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

# Lấy face_id từ tên người
@app.route('/get_face_id_by_name', methods=['POST'])
def get_face_id_by_name():
    data = request.json
    name = data.get("name")
    if not name:
        return jsonify({"error": "Thiếu tên"}), 400
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT face_id FROM faces WHERE name=?", (name,))
    row = cursor.fetchone()
    conn.close()
    return jsonify({"face_id": row[0]}) if row else jsonify({"error": "Không tìm thấy face_id"}), 404

# Bật/tắt chế độ tự động nhận diện khuôn mặt
auto_recognition_enabled = False
@app.route('/set_auto_recognition', methods=['POST'])
def set_auto_recognition():
    global auto_recognition_enabled
    data = request.json
    auto_recognition_enabled = data.get("enabled", False)
    return jsonify({"message": "Chế độ tự động đã được " + ("bật" if auto_recognition_enabled else "tắt")})

# Hàm chạy nền để tự động nhận diện khuôn mặt từ webcam
def auto_face_recognition():
    cap = cv2.VideoCapture(1)
    if not cap.isOpened():
        print("Không thể mở webcam!")
        return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    while True:
        if not auto_recognition_enabled:
            time.sleep(0.5)
            continue
        ret, frame = cap.read()
        if not ret:
            continue
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb_frame, model="hog")
        if not face_locations:
            time.sleep(0.5)
            continue

        # Lấy khuôn mặt đầu tiên
        top, right, bottom, left = face_locations[0]
        face_img = rgb_frame[top:bottom, left:right]
        encodings = face_recognition.face_encodings(face_img)
        if not encodings:
            continue
        new_face_encoding = encodings[0]

        matched = False
        for file in os.listdir(UPLOAD_FOLDER):
            file_path = os.path.join(UPLOAD_FOLDER, file)
            try:
                existing_image = face_recognition.load_image_file(file_path)
                existing_encodings = face_recognition.face_encodings(existing_image)
                if existing_encodings and face_recognition.compare_faces([existing_encodings[0]], new_face_encoding, tolerance=0.4)[0]:
                    face_id = file.split("_", 1)[0]
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    # Lấy thông tin đã lưu trong bảng faces
                    cursor.execute("SELECT name, dob, gender FROM faces WHERE face_id=?", (face_id,))
                    row = cursor.fetchone()
                    if row:
                        name, dob, gender = row
                    else:
                        name, dob, gender = ("Unknown", "", "")
                    # Nếu gender hoặc dob chưa có, thử lấy từ sự kiện "Vào" gần nhất
                    if not gender or not dob:
                        cursor.execute(
                            "SELECT recognized_faces FROM events WHERE recognized_faces LIKE ? AND event_type = 'Vào' ORDER BY timestamp DESC LIMIT 1",
                            (f'%"{face_id}"%',)
                        )
                        event_row = cursor.fetchone()
                        if event_row:
                            event_info = json.loads(event_row[0])
                            gender = event_info.get("gender", gender)
                            dob = event_info.get("dob", dob)
                    
                    # Lấy ảnh từ sự kiện cuối cùng nếu có
                    cursor.execute("SELECT recognized_faces FROM events WHERE recognized_faces LIKE ? ORDER BY timestamp DESC LIMIT 1",
                                   (f'%"{face_id}"%',))
                    event_row = cursor.fetchone()
                    if event_row:
                        last_face_info = json.loads(event_row[0])
                        face_image = last_face_info.get("face_image")
                        if not face_image:
                            face_image = url_for('uploaded_file', filename=os.path.basename(file_path))
                    else:
                        face_image = url_for('uploaded_file', filename=os.path.basename(file_path))
                    conn.close()
                    
                    # Kiểm tra sự kiện cuối cùng của khuôn mặt để chuyển đổi giữa "Vào" và "Ra"
                    conn = sqlite3.connect(DB_NAME)
                    cursor = conn.cursor()
                    cursor.execute("SELECT event_type FROM events WHERE recognized_faces LIKE ? ORDER BY timestamp DESC LIMIT 1",
                                   (f'%"{face_id}"%',))
                    last_event = cursor.fetchone()
                    conn.close()
                    
                    # Nếu sự kiện cuối cùng là "Vào", chuyển sang "Ra"
                    if last_event and last_event[0] == "Vào":
                        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        face_info = {
                            "face_id": face_id,
                            "name": name,
                            "dob": dob,
                            "gender": gender,
                            "face_image": face_image
                        }
                        conn = sqlite3.connect(DB_NAME)
                        cursor = conn.cursor()
                        cursor.execute("INSERT INTO events (timestamp, recognized_faces, count, event_type) VALUES (?, ?, ?, ?)",
                                       (timestamp, json.dumps(face_info), 1, "Ra"))
                        conn.commit()
                        conn.close()
                    matched = True
                    break
            except Exception as e:
                print(f"Lỗi khi xử lý file {file}: {e}")
                continue
        # Giảm thời gian chờ nếu nhận diện thành công
        if matched:
            time.sleep(0.2)
        else:
            time.sleep(0.5)
        # Nếu không có match, tạo mới khuôn mặt
        if not matched:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            face_id = str(uuid.uuid4())
            face_filename = os.path.join(UPLOAD_FOLDER, f"{face_id}_{timestamp}.jpg")
            Image.fromarray(face_img).save(face_filename)
            face_url = url_for('uploaded_file', filename=os.path.basename(face_filename))
            with app.app_context():
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("INSERT INTO faces (face_id, name, dob, gender, timestamp) VALUES (?, ?, ?, ?, ?)",
                               (face_id, "", "", "", timestamp))
                face_info = {
                    "face_id": face_id,
                    "name": "",
                    "dob": "",
                    "gender": "",
                    "face_image": face_url
                }
                cursor.execute("INSERT INTO events (timestamp, recognized_faces, count, event_type) VALUES (?, ?, ?, ?)",
                               (timestamp, json.dumps(face_info), 1, "Vào"))
                conn.commit()
                conn.close()
    cap.release()


# Khởi động luồng tự động nhận diện khuôn mặt
threading.Thread(target=auto_face_recognition, daemon=True).start()

# **Chạy ứng dụng Flask**
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)