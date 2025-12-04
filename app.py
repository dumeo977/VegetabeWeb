import os
import json
import threading
import time
import base64
from flask import Flask, render_template, request, session, redirect, url_for, send_from_directory, make_response, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename

# --- Định nghĩa Thư Mục Gốc của Ứng Dụng (BASE_DIR) ---
# Dùng để xây dựng đường dẫn tuyệt đối cho các file dữ liệu
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# --------------------------------------------------------

# --- CẤU HÌNH SHOP VÀ CHAT ---
UPLOAD_FOLDER = 'uploads'
# FIX 1: Dùng đường dẫn tuyệt đối cho HISTORY_FILE
HISTORY_FILE = os.path.join(BASE_DIR, 'chat_history.json') 
# FIX 2: Dùng đường dẫn tuyệt đối cho PRODUCTS_FILE
PRODUCTS_FILE = os.path.join(BASE_DIR, "products.json") 
MAX_FILE_SIZE = 100 * 1024 * 1024 # 100 MB
ADMIN_PASSWORD = 'dumao123'


# Khởi tạo Flask và TẮT static_folder mặc định (Quan trọng cho Shop)
app = Flask(__name__, static_folder=None)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE + 1024 * 1024

socketio = SocketIO(app, cors_allowed_origins="*", ping_interval=25, ping_timeout=60)

# Tạo thư mục uploads nếu chưa có
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- CHAT GLOBALS (Lấy từ app.py Chat) ---
clients = {}
banned_users = {}
clients_lock = threading.Lock()
chat_history = []
message_id_counter = 0

# --- SHOP GLOBALS ---
PRODUCTS_DATA = {}

# --- HÀM TẢI DỮ LIỆU ---

def load_history():
    global chat_history, message_id_counter
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                chat_history = json.load(f)
                if chat_history:
                    message_id_counter = max(int(msg.get('id', 0)) for msg in chat_history) + 1
        except json.JSONDecodeError:
            print(f"🚨 ERROR: {HISTORY_FILE} invalid JSON, starting fresh.")
            chat_history = []
        except Exception as e:
            print(f"🚨 ERROR loading history: {e}")
            chat_history = []
    print(f"✅ Chat: Loaded {len(chat_history)} messages from {HISTORY_FILE}.")

def save_history():
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(chat_history, f, indent=4, ensure_ascii=False)

def get_new_message_id():
    global message_id_counter
    current_id = message_id_counter
    message_id_counter += 1
    return str(current_id)

def load_products():
    global PRODUCTS_DATA
    try:
        with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
            PRODUCTS_DATA = json.load(f)
        print(f"✅ Shop: Loaded {len(PRODUCTS_DATA)} products from {PRODUCTS_FILE}.")
    except FileNotFoundError:
        print(f"🚨 WARNING: {PRODUCTS_FILE} not found (path: {PRODUCTS_FILE}). Shop API will be empty.")
        PRODUCTS_DATA = {}
    except json.JSONDecodeError:
        print(f"🚨 ERROR: {PRODUCTS_FILE} invalid JSON, starting empty.")
        PRODUCTS_DATA = {}

# --- UTILS (Giữ nguyên) ---
def broadcast_active_users():
    active_users = []
    with clients_lock:
        for info in clients.values():
            if info.get('name'):
                active_users.append({
                    'name': info['name'],
                    'is_admin': info['authenticated']
                })
    socketio.emit('active_users', active_users, to=None)

def get_client_info_by_name(target_name):
    with clients_lock:
        for sid, info in clients.items():
            if info.get('name') == target_name:
                return sid, info
    return None, None

def get_client_info_by_sid(sid):
    with clients_lock:
        return clients.get(sid, {})

# --- CÁC ROUTE WEB (Giữ nguyên) ---

@app.route('/')
def index_shop():
    return send_from_directory('public', 'index.html')

@app.route('/api/products')
def get_products():
    return jsonify(PRODUCTS_DATA)

@app.route('/assets/<path:filename>')
def serve_assets(filename):
    return send_from_directory('public/assets', filename)

@app.route('/product/<int:product_id>')
def product_detail(product_id):
    return send_from_directory('public', 'product.html')

@app.route('/chat')
def chat_room():
    return send_from_directory('public', 'chat.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/reset_session')
def reset_session_route():
    session.clear()
    resp = make_response(redirect(url_for('index_shop'))) 
    resp.set_cookie('user_name', '', expires=0)
    resp.set_cookie('is_admin', '', expires=0)
    return resp

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('public', path)

# --- SOCKETIO HANDLERS (Giữ nguyên) ---

@socketio.on('connect')
def handle_connect():
    sid = request.sid
    ip = request.remote_addr
    with clients_lock:
        clients[sid] = {'name': None, 'authenticated': False, 'ip': ip, 'join_time': time.time(), 'client_info_received': False}

@socketio.on('client_info')
def handle_client_info(data):
    sid = request.sid
    with clients_lock:
        if sid in clients:
            clients[sid]['os'] = data.get('os', 'N/A')
            clients[sid]['battery'] = data.get('battery_level', 'N/A')
            clients[sid]['charging'] = data.get('charging', False)
            clients[sid]['client_info_received'] = True
            log_name = clients[sid].get('name') or sid
            print(f"Client Log: [{log_name}] OS: {clients[sid]['os']}, Pin: {clients[sid]['battery']}% (Sạc: {clients[sid]['charging']})")

@socketio.on('join')
def handle_join(data):
    sid = request.sid
    raw_name = data['name']
    
    display_name = raw_name if raw_name else f"Đếch có tên #{sid[:4]}"
    
    with clients_lock:
        info = clients.get(sid)
        if not info:
            print(f"🚨 Lỗi: SID {sid} chưa có trong clients khi Join.")
            return 

        if display_name in banned_users:
            banned_time = banned_users[display_name]
            if time.time() - banned_time < 3600:
                emit('system_message', {'message': f"Mày bị ban rồi, chờ {(3600 - (time.time() - banned_time)) / 60:.1f} phút nữa nha."}, room=sid)
                return

        info['name'] = display_name
        info['authenticated'] = data.get('is_admin', False)

        if info.get('client_info_received'):
            print(f"Client Log: [JOIN] [{info['name']}] OS: {info['os']}, Pin: {info['battery']}% (Sạc: {info['charging']})")

    emit('chat_history', chat_history)
    
    socketio.emit('message', {
        'id': get_new_message_id(),
        'name': 'System',
        'message': f"**{display_name}** mới lạc vào đây",
        'is_admin': False
    }, to=None)
    
    broadcast_active_users()

    socketio.emit('message', {
        'id': get_new_message_id(),
        'name': 'System',
        'message': f"**{display_name}** mới lạc vào đây",
        'is_admin': False
    }, to=None)
    broadcast_active_users()


@socketio.on('message')
def handle_message(data):
    sid = request.sid
    with clients_lock:
        client_info = clients.get(sid)
        if not client_info or not client_info.get('name'): return
    
    msg_id = get_new_message_id()
    message_data = {
        'id': msg_id,
        'name': client_info['name'],
        'message': data['message'],
        'is_admin': client_info['authenticated']
    }
    chat_history.append(message_data)
    save_history()
    socketio.emit('message', message_data, to=None)


@socketio.on('send_file')
def handle_file(data):
    sid = request.sid
    with clients_lock:
        client_info = clients.get(sid)
        if not client_info or not client_info.get('name'): return

    try:
        file_data_url = data['file_data']
        file_type = data['file_type']
        
        header, encoded = file_data_url.split(',', 1)
        file_bytes = base64.b64decode(encoded)
        
        ext = file_type.split('/')[-1]
        if ext in ['jpeg', 'jpg']: ext = 'jpg'
        if ext in ['mpga']: ext = 'mp3'
        if ext.startswith('vnd.'):
            ext = file_type.split('.')[-1].split(';')[0]
        
        raw_filename = f"{client_info['name']}_{int(time.time())}"
        safe_filename = secure_filename(raw_filename) + f".{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
        
        if len(file_bytes) > MAX_FILE_SIZE:
            emit('system_message', {'message': f"File quá to, giới hạn là {MAX_FILE_SIZE / 1024 / 1024}MB thôi mày!"})
            return

        with open(filepath, 'wb') as f:
            f.write(file_bytes)

        msg_id = get_new_message_id()
        file_url = url_for('uploaded_file', filename=safe_filename)
        
        file_message = {
            'id': msg_id,
            'name': client_info['name'],
            'file_path': file_url,
            'file_type': file_type,
            'is_admin': client_info['authenticated']
        }
        
        chat_history.append(file_message)
        save_history()
        socketio.emit('receive_file', file_message, to=None)

    except Exception as e:
        print(f"Lỗi xử lý file: {e}")
        emit('system_message', {'message': "Lỗi gửi file. Thử lại đi đm!"})


@socketio.on('delete_message')
def handle_delete_message(data):
    sid = request.sid
    msg_id_to_delete = data.get('id')
    
    with clients_lock:
        client_info = clients.get(sid)
        if not client_info or not client_info.get('authenticated'): return

    global chat_history
    original_length = len(chat_history)
    chat_history = [msg for msg in chat_history if str(msg.get('id')) != str(msg_id_to_delete)]
    
    if len(chat_history) < original_length:
        save_history()
        socketio.emit('delete_message', {'id': msg_id_to_delete}, to=None)
        print(f"[Admin Log] {client_info['name']} đã xóa tin nhắn ID: {msg_id_to_delete}")
        

@socketio.on('command')
def handle_command(data):
    sender_sid = request.sid
    command_text = data['command'].strip()
    cmd, *args = command_text.split()
    cmd = cmd.lower()
    
    client_info = get_client_info_by_sid(sender_sid)
    is_admin = client_info.get("authenticated", False)
    
    if cmd == "pass":
        if args and args[0] == ADMIN_PASSWORD:
            clients[sender_sid]["authenticated"] = True
            emit('authenticated_response', {'message': "Welcome you stupid admin gg stfu"})
            emit('authenticated', True, to=None)
            broadcast_active_users()
            return
        else:
            emit('system_message', {'message': "Định làm gì, cút đi đm"})
            return
    
    if not is_admin:
        emit('system_message', {'message': f"Lệnh không hợp lệ: /{cmd}. Ko có đâu bé ơi m ko phải admin."})
        return

    response_message = ""
    target_name = args[0] if args else None
    target_sid, target_info = get_client_info_by_name(target_name)

    if cmd == "kick":
        if not target_sid: response_message = f"Không tìm thấy user '{target_name}' đang online."
        else:
            emit('system_message', {'message': f"CÚT."}, room=target_sid)
            socketio.disconnect(target_sid)
            response_message = f"Đã đá user **{target_name}** ra khỏi phòng."
    elif cmd == "ban":
        if not target_name: response_message = "Cú pháp: /ban [tên user]"
        else:
            banned_users[target_name] = time.time()
            if target_sid:
                emit('system_message', {'message': f"1 tiếng nữa quay lại mày bị ban rồi đm"}, room=target_sid)
                socketio.disconnect(target_sid)
            response_message = f"Đã cấm user **{target_name}** trong 1 giờ. ⛔"
    elif cmd == "promote":
        if not target_sid: response_message = f"Không tìm thấy user '{target_name}' đang online."
        else:
            target_info["authenticated"] = True
            emit('system_message', {'message': "Mày vừa được Admin phong làm Admin phụ! Dùng /pass để xác nhận."}, room=target_sid)
            emit('authenticated', True, room=target_sid)
            response_message = f"Đã thăng chức cho **{target_name}** lên Admin."
            broadcast_active_users()
    elif cmd == "demote":
        if not target_sid: response_message = f"Không tìm thấy user '{target_name}' đang online."
        elif target_sid == sender_sid: response_message = "Mày không thể tự giáng cấp chính mình!"
        else:
            target_info["authenticated"] = False
            emit('system_message', {'message': "Mày vừa bị Admin giáng cấp xuống thành thằng oắt con."}, room=target_sid)
            emit('authenticated', False, room=target_sid)
            response_message = f"Đã giáng chức **{target_name}** xuống thằng oắt con."
            broadcast_active_users()
    elif cmd == "clearchat":
        global chat_history
        chat_history = []
        save_history()
        response_message = "Đã dọn dẹp sạch sẽ lịch sử chat."
        socketio.emit('clearchat_complete', to=None)
    else:
        response_message = f"Lệnh Admin không hợp lệ: /{cmd}"

    emit('system_message', {'message': response_message})


@socketio.on('disconnect')
def on_disconnect(reason):
    sid = request.sid
    user_name = None
    was_admin = False
    
    with clients_lock:
        if sid in clients:
            info = clients[sid]
            user_name = info.get('name')
            was_admin = info.get('authenticated', False)
            clients.pop(sid, None)
            
    if user_name:
        print(f"[{'Admin' if was_admin else 'User'}] {user_name} ngắt kết nối. Lý do: {reason}")
        socketio.emit('message', {
            'id': get_new_message_id(),
            'name': 'System',
            'message': f"**{user_name}** đã rời phòng",
            'is_admin': False
        }, to=None)
        
    broadcast_active_users()


# --- CONSOLE MANAGER (CHỈ DÀNH CHO FLASK DEV SERVER) ---
# KHÔNG CHẠY CÁI NÀY VỚI GUNICORN VÌ NÓ SẼ LỖI MULTI-PROCESSING
def console_manager():
    """Chạy thread console riêng để nhận lệnh Admin."""
    while True:
        try:
            command_line = input("Server Console > ")
            if not command_line: continue
            
            cmd, *args = command_line.split()
            cmd = cmd.lower()
            
            response = ""
            if cmd == 'list':
                response = "Danh sách người dùng đang kết nối:\n"
                online_users = []
                with clients_lock:
                    for sid, info in clients.items():
                        name = info.get('name', 'N/A')
                        display_name = name if name != 'N/A' else f"Chưa nhập tên ({sid[:4]})"
                        is_admin = info.get('authenticated', False)
                        ip = info.get('ip', 'N/A')
                        os_info = info.get('os', 'N/A')
                        battery = info.get('battery', 'N/A')
                        charging = "Sạc" if info.get('charging', False) else "Không Sạc"
                        
                        admin_tag = " (ADMIN 👑)" if is_admin else ""
                        online_users.append(f"- {display_name}{admin_tag} | IP: {ip} | OS: {os_info} | Pin: {battery}% ({charging})")
                
                response += "\n".join(online_users) if online_users else "Không có ai đang kết nối."
            elif cmd == 'ban':
                if not args: response = "Cú pháp: ban [tên user]"
                else:
                    target_name = args[0]
                    target_sid, target_info = get_client_info_by_name(target_name)
                    
                    banned_users[target_name] = time.time()
                    if target_sid:
                        socketio.emit('system_message', {'message': f"M bị t ban bằng terminal"}, room=target_sid)
                        socketio.disconnect(target_sid)
                    
                    response = f"Đã cấm user **{target_name}** trong 1 giờ. ⛔"
            elif cmd == 'unban':
                if not args: response = "Cú pháp: unban [tên user]"
                else:
                    target_name = args[0]
                    if target_name in banned_users:
                        del banned_users[target_name]
                        response = f"Đã bỏ cấm user **{target_name}**."
                    else:
                        response = f"User **{target_name}** không có trong danh sách cấm."
            elif cmd == 'bannedlist':
                if banned_users:
                    response = "Danh sách user đang bị cấm:\n"
                    list_items = []
                    for name, ban_time in list(banned_users.items()):
                        remaining = (3600 - (time.time() - ban_time)) / 60
                        if remaining > 0:
                            list_items.append(f"- {name} (còn {remaining:.1f} phút)")
                        else:
                            del banned_users[name]
                    response += "\n".join(list_items) if list_items else "Không có ai đang bị cấm."
                else:
                    response = "Không có ai đang bị cấm."
            else:
                response = f"Lệnh không hợp lệ: /{cmd} (Các lệnh hợp lệ: list, ban, unban, bannedlist)"
            
            print(f"Server Response: {response}")
            
        except Exception as e:
            print(f"Server Console Error: {e}")
            time.sleep(0.1)

# --- KHỞI TẠO DỮ LIỆU BẮT BUỘC (DÀNH CHO GUNICORN VÀ FLASK DEV SERVER) ---
# Gunicorn sẽ chạy các hàm này ngay khi import module
load_history() 
load_products() 

# --- KHỞI ĐỘNG (CHỈ DÀNH CHO FLASK DEV SERVER) ---
if __name__ == '__main__':
    print(f"Server Console > Thư mục gốc BASE_DIR: {BASE_DIR}")
    
    # Chỉ chạy Console Manager khi dùng Flask Dev Server
    console_thread = threading.Thread(target=console_manager, daemon=True)
    console_thread.start()
    
    # Chỉ chạy socketio.run khi dùng python3 app.py
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
