import base64
from flask import Flask, request, jsonify
from flask_cors import CORS
from mongoengine import connect, DoesNotExist
from schema.people import People
from schema.track import Track
import numpy as np
import bson
import cv2
from face_service import get_one_face, verify_face
import jwt
from datetime import datetime, timedelta, time
from functools import wraps
from flask_socketio import SocketIO, emit
import threading
from kafka import KafkaConsumer
import kafka_service.config
import json
import calendar

app = Flask(__name__)
app.config["SECRET_KEY"] = "vdchinhs"
CORS(app, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*")

CONSUMER = None
HOUR = 17
MINUTE = 0
WORKING_HOURS = 8
LUNCH_BREAK = 1

connect(db="people_manage", host="mongodb://localhost:27017")
topic_name = "cam"


def decode_token(token):
    try:
        payload = jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
        return payload["username"]
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token:
            return jsonify({"message": "Token không được cung cấp"}), 403
        try:
            token = token.split(" ")[1]
            username = decode_token(token)
            if not username:
                return jsonify({"message": "Token không hợp lệ hoặc đã hết hạn"}), 403
        except Exception as e:
            return jsonify({"message": "Có lỗi xảy ra khi xác thực token"}), 403
        return f(*args, **kwargs)

    return decorated


def get_total_working_hours(people_id, month, year):
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)

    tracks = Track.objects(
        peopleid=people_id, timestamp__gte=start_date, timestamp__lt=end_date
    )

    daily_work_hours = {}
    cutoff_time = time(HOUR, MINUTE)
    for track in tracks:
        day = track.timestamp.date()
        if day not in daily_work_hours:
            daily_work_hours[day] = {"min": track.timestamp, "max": track.timestamp}
        else:
            daily_work_hours[day]["min"] = min(
                daily_work_hours[day]["min"], track.timestamp
            )
            daily_work_hours[day]["max"] = max(
                daily_work_hours[day]["max"], track.timestamp
            )

    total_hours = 0
    filtered_daily_hours = {}
    for day, times in daily_work_hours.items():
        if times["min"].time() <= cutoff_time:
            if times["max"].time() > cutoff_time:
                work_duration = (times["max"] - times["min"]).total_seconds() / 3600
                filtered_daily_hours[day] = work_duration
                total_hours += work_duration

    return round(total_hours, 2)


def get_days_in_month(month, year):
    _, num_days = calendar.monthrange(year, month)
    return num_days


def days_from_start_of_month(year: int, month: int, day: int) -> int:
    date = datetime(year, month, day)
    start_of_month = datetime(year, month, 1)
    delta = date - start_of_month
    return delta.days + 1


def convert_month_to_abbreviation(month_name: str) -> str:
    full_to_abbreviation = {
        "January": "Jan",
        "February": "Feb",
        "March": "Mar",
        "April": "Apr",
        "May": "May",
        "June": "Jun",
        "July": "Jul",
        "August": "Aug",
        "September": "Sep",
        "October": "Oct",
        "November": "Nov",
        "December": "Dec",
    }
    return full_to_abbreviation.get(month_name, "Invalid month name")


def calculate_employee_total_overtime(employee_id, month, year):
    first_day = datetime(year, month, 1)
    last_day = datetime(year, month, calendar.monthrange(year, month)[1], 23, 59, 59)

    people = People.objects(id=employee_id).first()
    if not people:
        return 0

    tracks = Track.objects(
        peopleid=employee_id, timestamp__gte=first_day, timestamp__lte=last_day
    ).order_by("timestamp")

    if len(tracks) < 2:
        return 0

    daily_tracks = {}
    cutoff_time = time(HOUR, MINUTE)

    for track in tracks:
        date_key = track.timestamp.date()
        if date_key not in daily_tracks:
            daily_tracks[date_key] = []
        daily_tracks[date_key].append(track)

    total_overtime_hours = 0

    for date, day_tracks in daily_tracks.items():
        day_tracks_sorted = sorted(day_tracks, key=lambda x: x.timestamp)

        first_track = day_tracks_sorted[0]
        last_track = day_tracks_sorted[-1]

        if first_track.timestamp.time() > cutoff_time:
            continue

        work_end_time = datetime.combine(date, datetime.min.time()).replace(
            hour=HOUR, minute=MINUTE
        )

        if last_track.timestamp > work_end_time:
            daily_overtime = last_track.timestamp - work_end_time
            total_overtime_hours += daily_overtime.total_seconds() / 3600

    return round(total_overtime_hours, 3)


### WEB
@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"message": "Thiếu thông tin đăng nhập"}), 400

    if username == "admin" and password == "1":
        token = jwt.encode(
            {
                "username": username,
                "exp": datetime.utcnow() + timedelta(hours=1),
            },
            app.config["SECRET_KEY"],
            algorithm="HS256",
        )
        return jsonify({"message": "Đăng nhập thành công", "token": token}), 200
    else:
        return jsonify({"message": "Tên đăng nhập hoặc mật khẩu không đúng"}), 401


@app.route("/add_people", methods=["POST"])
@token_required
def add_user():
    name = request.form.get("name")
    role = request.form.get("role")
    image_file = request.files.get("straight_photo")

    if not name or not image_file:
        return jsonify({"message": "Thiếu thông tin cần thiết"}), 400

    image_binary = image_file.read()

    nparr = np.frombuffer(image_binary, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    face = get_one_face(image)
    # cv2.imwrite("image.jpg", face[0].face_align)
    if face is None:
        return (
            jsonify({"message": "Không tìm thấy khuôn mặt, hoặc ảnh chụp quá gần"}),
            400,
        )
    binary_face_embedding = bson.Binary(face[0].embedding.astype(np.float32).tobytes())
    binary_face_straight = bson.Binary(face[0].face_align.astype(np.uint8).tobytes())

    user = People(
        name=name,
        role=role,
        embeddingface=binary_face_embedding,
        facestraight=binary_face_straight,
    )
    user.save()
    return jsonify({"message": "Thêm thành công!", "user_id": str(user.id)})


@app.route("/update_people", methods=["PUT"])
@token_required
def update_user():
    id = request.form.get("id")
    if not id:
        return jsonify({"error": "User ID not provided"}), 400

    new_name = request.form.get("name")
    new_role = request.form.get("role")
    image_changed = request.form.get("imagechanged")
    image_file = request.files.get("straight_photo")

    try:
        user = People.objects.get(id=id)
        user.name = new_name
        user.role = new_role

        if image_changed == "true":
            image_binary = image_file.read()
            nparr = np.frombuffer(image_binary, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            face = get_one_face(image)
            if face is None:
                return (
                    jsonify(
                        {"message": "Không tìm thấy khuôn mặt, hoặc ảnh chụp quá gần"}
                    ),
                    400,
                )
            binary_face_embedding = bson.Binary(
                face[0].embedding.astype(np.float32).tobytes()
            )
            binary_face_straight = bson.Binary(
                face[0].face_align.astype(np.uint8).tobytes()
            )
            user.embeddingface = binary_face_embedding
            user.facestraight = binary_face_straight

        user.save()
        return jsonify({"message": "Sửa thành công!!"})
    except DoesNotExist:
        return jsonify({"message": "Không tìm thấy người"}), 404
    except Exception as e:
        return jsonify({"message": str(e)}), 500


@app.route("/delete_people", methods=["DELETE"])
@token_required
def delete_user():
    user_id = request.form.get("id")
    print(user_id)
    try:
        Track.objects(peopleid=user_id).delete()
        user = People.objects.get(id=user_id)
        user.delete()
        return jsonify({"message": "Xóa thành công!!!"})
    except DoesNotExist:
        return jsonify({"message": "Không tìm thấy người"}), 404
    except Exception as e:
        return jsonify({"message": str(e)}), 500


@app.route("/peoples/<int:page>/<int:per_page>", methods=["GET"])
def get_users(page, per_page):
    total_users = People.objects.count()
    total_pages = (total_users + per_page - 1) // per_page

    users = People.objects.skip((page - 1) * per_page).limit(per_page)
    result = []

    for idx, user in enumerate(users):
        np_array_from_db = np.frombuffer(user.facestraight, dtype=np.uint8)
        np_array_from_db = np_array_from_db.reshape((256, 256, 3))
        _, buffer = cv2.imencode(".jpg", np_array_from_db)

        base64_image = base64.b64encode(buffer).decode("utf-8")
        result.append(
            {
                "id": str(user.id),
                "name": user.name,
                "role": user.role,
                # 'embeddingface': str(user.embeddingface),
                "facestraight": base64_image,
            }
        )

    return jsonify(
        {
            "total": total_users,
            "pages": total_pages,
            "current_page": page,
            "peoples": result,
        }
    )


@app.route("/namespeople", methods=["GET"])
def names_people():
    peoples = [{"id": str(person.id), "name": person.name} for person in People.objects]
    return jsonify(peoples)


@app.route("/imagebyid/<string:id>", methods=["GET"])
def get_image_buy_id(id):
    user = People.objects.get(id=id)

    np_array_from_db = np.frombuffer(user.facestraight, dtype=np.uint8)
    np_array_from_db = np_array_from_db.reshape((256, 256, 3))
    _, buffer = cv2.imencode(".jpg", np_array_from_db)
    base64_image = base64.b64encode(buffer).decode("utf-8")

    return jsonify(
        {
            "image": base64_image,
        }
    )


@app.route("/detection", methods=["POST"])
def detection():
    image_file = request.files.get("straight_photo")

    if not image_file:
        return jsonify({"error": "Thiếu thông tin cần thiết"}), 400

    image_binary = image_file.read()
    nparr = np.frombuffer(image_binary, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    face = get_one_face(image)
    face_embedding = face[0].embedding

    users = People.objects()
    db_embedding = []
    db_id = []

    for idx, user in enumerate(users):
        np_array_from_db = np.frombuffer(user.embeddingface, dtype=np.float32)
        db_id.append(user.id)
        db_embedding.append(np_array_from_db)

    id, confi = verify_face(face_embedding, db_embedding, db_id)
    binary_face_straight = bson.Binary(face[0].face_align.astype(np.uint8).tobytes())

    if id == None:
        track = Track(
            peopleid=None,
            name="Unknown",
            zone=0,
            type="entry",
            timestamp=datetime.now(),
            facesnapshot=binary_face_straight,
        )
    else:
        user = People.objects.get(id=id)
        track = Track(
            peopleid=user.id,
            name=user.name,
            zone=0,
            type="entry",
            timestamp=datetime.now(),
            facesnapshot=binary_face_straight,
        )
    track.save()
    socketio.emit("response", {"data": "New Update", "zone": track.zone})

    return jsonify(
        {
            "message": "Detection Succserr",
            "user": {
                "id": str(user.id),
                "name": user.name,
                "conficent": float(confi),
                "facestraight": "facestra",
            },
        }
    )


# TRACKER
@app.route("/unique_zones", methods=["GET"])
def get_unique_zones():
    unique_zones = Track.objects.distinct("zone")

    return jsonify({"unique_zones": unique_zones})


@app.route("/add_track", methods=["POST"])
def add_track():
    data = request.form
    people_id = data.get("peopleid")

    facesnapshot = request.files.get("facesnapshot").read()
    nparr = np.frombuffer(facesnapshot, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    # binary_face_straight = bson.Binary(image.astype(np.uint8).tobytes())

    _, buffer = cv2.imencode(".jpg", image)
    cv2.imwrite("image.jpg", buffer)
    binary_face_snapshot = buffer.tobytes()

    if people_id:
        pp = People.objects(id=people_id).first()
        ppid = pp.id
        name = pp.name
    else:
        ppid = None
        name = "Unkown"

    track = Track(
        peopleid=ppid, name=name, type="entry", facesnapshot=binary_face_snapshot
    )

    track.save()
    return jsonify({"message": "Track created successfully", "track_id": str(track.id)})


@app.route(
    "/tracks/<int:page>/<int:per_page>/<string:gate>/<string:startdate>/<string:enddate>",
    methods=["GET"],
)
def get_tracks(page, per_page, gate, startdate, enddate):
    if startdate != "":
        start_date = datetime.strptime(startdate, "%Y-%m-%d")
        start_date = datetime.combine(start_date, datetime.min.time())

    if enddate != "":
        end_date = datetime.strptime(enddate, "%Y-%m-%d")
        end_date = datetime.combine(end_date, datetime.max.time())

    gate_select = int(gate)

    total_tracks = Track.objects(
        timestamp__gte=start_date, timestamp__lte=end_date, zone=gate_select
    ).count()
    total_pages = (total_tracks + per_page - 1) // per_page
    tracks = (
        Track.objects(
            timestamp__gte=start_date, timestamp__lte=end_date, zone=gate_select
        )
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    result = []

    for idx, user in enumerate(tracks):
        np_array_from_db = np.frombuffer(user.facesnapshot, dtype=np.uint8)
        np_array_from_db = np_array_from_db.reshape((256, 256, 3))
        _, buffer = cv2.imencode(".jpg", np_array_from_db)

        base64_image = base64.b64encode(buffer).decode("utf-8")
        result.append(
            {
                "id": str(user.id),
                "peopleid": str(user.peopleid.id) if user.peopleid else None,
                "time": user.timestamp,
                "name": (
                    str(People.objects(id=user.peopleid.id)[0].name)
                    if user.peopleid
                    else "Unknown"
                ),
                "zone": user.zone,
                "type": user.type,
                "facesnapshot": base64_image,
            }
        )

    return jsonify(
        {
            "total": total_tracks,
            "pages": total_pages,
            "current_page": page,
            "tracks": result,
        }
    )


# CHECKIN
@app.route("/checkinbyday/<string:datestr>", methods=["GET"])
def checkin_by_day(datestr):
    if datestr != "":
        datestr = datetime.strptime(datestr, "%Y-%m-%d")
        date = datetime.combine(datestr, datetime.min.time())

    cutoff_time = datetime.combine(date, datetime.min.time()) + timedelta(
        hours=HOUR, minutes=MINUTE
    )

    all_people = People.objects()
    track_records = Track.objects(
        timestamp__gte=date, timestamp__lt=date + timedelta(days=1)
    )

    people_data = {}
    for person in all_people:
        person_id = str(person.id)
        np_array_from_db = np.frombuffer(person.facestraight, dtype=np.uint8)
        np_array_from_db = np_array_from_db.reshape((256, 256, 3))
        _, buffer = cv2.imencode(".jpg", np_array_from_db)

        base64_image = base64.b64encode(buffer).decode("utf-8")
        people_data[person_id] = {
            "id": person_id,
            "name": person.name,
            "role": person.role,
            "checkin": "-",
            "checkout": "-",
            "imageface": base64_image,
            "status": "Not Checked In",
        }

    for record in track_records:
        if record.peopleid:
            person_id = str(record.peopleid.id)
            if person_id in people_data:
                # Nếu record.timestamp trước 17h30 => ghi nhận checkin
                if record.timestamp <= cutoff_time:
                    if people_data[person_id]["checkin"] == "-":
                        people_data[person_id]["checkin"] = record.timestamp
                        people_data[person_id]["status"] = "Checked In"
                    elif record.timestamp < people_data[person_id]["checkin"]:
                        people_data[person_id]["checkin"] = record.timestamp
                        people_data[person_id]["status"] = "Checked In"
                # Nếu record.timestamp sau 17h30 => chỉ ghi nhận checkout
                else:
                    if people_data[person_id]["checkout"] == "-":
                        people_data[person_id]["checkout"] = record.timestamp
                        people_data[person_id]["status"] = "Checked Out"
                    elif record.timestamp > people_data[person_id]["checkout"]:
                        people_data[person_id]["checkout"] = record.timestamp
                        people_data[person_id]["status"] = "Checked Out"

    return jsonify(list(people_data.values()))


# WORKING TIME
@app.route("/workingtime/<string:id>/<string:datestr>", methods=["GET"])
def workingtime(id, datestr):

    year, month, day = map(int, datestr.split("-"))
    current_month = datetime.now().month

    if month > 1:
        previous_month = month - 1
        previous_year = year
    else:
        previous_month = 12
        previous_year = year - 1

    if month > 6:
        start_month = 7
        end_month = 12
    else:
        start_month = 1
        end_month = 6

    if current_month == month:
        expected_working_hours_current_month = (
            days_from_start_of_month(year, month, day) * WORKING_HOURS
        )
    else:
        expected_working_hours_current_month = (
            get_days_in_month(month, year) * WORKING_HOURS
        )

    expected_working_hours_previous_month = (
        get_days_in_month(previous_month, previous_year) * WORKING_HOURS
    )

    if id == "all":
        all_people = People.objects()

        total_actual_working_hours_current_month = 0
        total_work_performance_current_month = 0
        total_over_time_hours_current_month = 0
        total_over_time_percent_current_month = 0
        total_increase_actual_working_hours = 0
        total_increase_work_performance = 0
        total_increase_over_time_hours = 0

        for employee in all_people:
            actual_working_hours_current_month = get_total_working_hours(
                employee.id, month, year
            )
            work_performance_current_month = round(
                (
                    actual_working_hours_current_month
                    / expected_working_hours_current_month
                )
                * 100,
                2,
            )

            over_time_hours_current_month = calculate_employee_total_overtime(
                employee.id, month, year
            )
            over_time_percent_current_month = round(
                (over_time_hours_current_month / expected_working_hours_current_month)
                * 100,
                3,
            )

            actual_working_hours_previous_month = get_total_working_hours(
                employee.id, previous_month, previous_year
            )
            work_performance_previous_month = round(
                (
                    actual_working_hours_previous_month
                    / expected_working_hours_previous_month
                )
                * 100,
                2,
            )
            over_time_hours_previous_month = calculate_employee_total_overtime(
                employee.id, previous_month, previous_year
            )
            increase_work_performance = (
                work_performance_current_month - work_performance_previous_month
            )

            if actual_working_hours_previous_month != 0:
                increase_actual_working_hours = round(
                    (
                        actual_working_hours_current_month
                        / actual_working_hours_previous_month
                    )
                    * 100,
                    2,
                )
            else:
                increase_actual_working_hours = 0

            if over_time_hours_previous_month != 0:
                increase_over_time_hours = round(
                    (over_time_hours_current_month / over_time_hours_previous_month)
                    * 100,
                    2,
                )
            else:
                increase_over_time_hours = 0

            total_actual_working_hours_current_month += (
                actual_working_hours_current_month
            )
            total_work_performance_current_month += work_performance_current_month
            total_over_time_hours_current_month += over_time_hours_current_month
            total_over_time_percent_current_month += over_time_percent_current_month
            total_increase_actual_working_hours += increase_actual_working_hours
            total_increase_work_performance += increase_work_performance
            total_increase_over_time_hours += increase_over_time_hours

        total_actual_working_hours_graphic = 0
        total_over_time_hours_graphic = 0
        working_time_data = []

        for month in range(start_month, end_month + 1):
            for employee in all_people:
                actual_working_hours_graphic = get_total_working_hours(
                    employee.id, month, year
                )
                over_time_hours_graphic = calculate_employee_total_overtime(
                    employee.id, month, year
                )

                total_actual_working_hours_graphic += actual_working_hours_graphic
                total_over_time_hours_graphic += over_time_hours_graphic

            working_time_data.append(
                {
                    "month": convert_month_to_abbreviation(calendar.month_name[month]),
                    "averageHours": total_actual_working_hours_graphic,
                    "overtime": total_over_time_hours_graphic,
                }
            )

        return jsonify(
            {
                "actual_working_hours": total_actual_working_hours_current_month,
                "increase_actual_working_hours": round(
                    total_increase_actual_working_hours, 2
                ),
                "work_performance": total_work_performance_current_month,
                "increase_work_performance": round(total_increase_work_performance, 2),
                "over_time_hours": total_over_time_hours_current_month,
                "increase_over_time_hours": (
                    round(total_increase_over_time_hours, 2)
                    if total_increase_over_time_hours >= 100
                    else -round(total_increase_over_time_hours, 2)
                ),
                "over_time_percent": round(total_over_time_percent_current_month, 3),
                "graph_data": working_time_data,
            }
        )

    actual_working_hours_current_month = get_total_working_hours(id, month, year)
    work_performance_current_month = round(
        (actual_working_hours_current_month / expected_working_hours_current_month)
        * 100,
        2,
    )

    over_time_hours_current_month = calculate_employee_total_overtime(id, month, year)
    over_time_percent_current_month = round(
        (over_time_hours_current_month / expected_working_hours_current_month) * 100, 3
    )

    actual_working_hours_previous_month = get_total_working_hours(
        id, previous_month, previous_year
    )
    work_performance_previous_month = round(
        (actual_working_hours_previous_month / expected_working_hours_previous_month)
        * 100,
        2,
    )
    over_time_hours_previous_month = calculate_employee_total_overtime(
        id, previous_month, previous_year
    )
    increase_work_performance = (
        work_performance_current_month - work_performance_previous_month
    )

    if actual_working_hours_previous_month != 0:
        increase_actual_working_hours = round(
            (actual_working_hours_current_month / actual_working_hours_previous_month)
            * 100,
            2,
        )
    else:
        increase_actual_working_hours = 0

    if over_time_hours_previous_month != 0:
        increase_over_time_hours = round(
            (over_time_hours_current_month / over_time_hours_previous_month) * 100, 2
        )
    else:
        increase_over_time_hours = 0

    working_time_data = []
    for month in range(start_month, end_month + 1):
        actual_working_hours_graphic = get_total_working_hours(id, month, year)
        over_time_hours_graphic = calculate_employee_total_overtime(id, month, year)

        working_time_data.append(
            {
                "month": convert_month_to_abbreviation(calendar.month_name[month]),
                "averageHours": actual_working_hours_graphic,
                "overtime": over_time_hours_graphic,
            }
        )

    return jsonify(
        {
            "actual_working_hours": actual_working_hours_current_month,
            "increase_actual_working_hours": increase_actual_working_hours,
            "work_performance": work_performance_current_month,
            "increase_work_performance": round(increase_work_performance, 2),
            "over_time_hours": over_time_hours_current_month,
            "increase_over_time_hours": (
                increase_over_time_hours
                if increase_over_time_hours >= 100
                else -increase_over_time_hours
            ),
            "over_time_percent": over_time_percent_current_month,
            "graph_data": working_time_data,
        }
    )


### WEBSOCKET
@socketio.on("message")
def handle_message(data):
    print("Received message: " + data)
    emit("response", {"data": "Server received: " + data}, broadcast=True)


def create_consumer():
    global CONSUMER
    if CONSUMER is None:
        CONSUMER = KafkaConsumer(
            topic_name,
            group_id="group1",
            bootstrap_servers=[kafka_service.config.kafka_ip],
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
    return CONSUMER


def decode_image(encoded_image):
    nparr = np.frombuffer(encoded_image, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return image


def consume_messages():
    consumer = create_consumer()
    for message in consumer:
        try:
            data_json = message.value.decode("utf-8")
            data = json.loads(data_json)

            image_bytes = base64.b64decode(data["image_data"])
            gate = data["gate"]
            typemove = data["type"]

            image = decode_image(image_bytes)
            cv2.imwrite(f"received_image_{message.offset}.jpg", image)
            face = get_one_face(image)
            if face is None:
                continue
            face_embedding = face[0].embedding

            users = People.objects()
            db_embedding = []
            db_id = []

            for idx, user in enumerate(users):
                np_array_from_db = np.frombuffer(user.embeddingface, dtype=np.float32)
                db_id.append(user.id)
                db_embedding.append(np_array_from_db)

            id, confi = verify_face(face_embedding, db_embedding, db_id)
            print(f"Id: {id}, Confidence: {confi}")

            binary_face_straight = bson.Binary(
                face[0].face_align.astype(np.uint8).tobytes()
            )
            if id == None:
                track = Track(
                    peopleid=None,
                    name="Unknown",
                    zone=gate,
                    type=typemove,
                    timestamp=datetime.now(),
                    facesnapshot=binary_face_straight,
                )
            else:
                user = People.objects.get(id=id)
                track = Track(
                    peopleid=user.id,
                    name=user.name,
                    zone=gate,
                    type=typemove,
                    timestamp=datetime.now(),
                    facesnapshot=binary_face_straight,
                )

            track.save()
            print(f"Đã gửi message tới Client")
            socketio.emit(
                "response",
                {"Data": "New people: " + str(track.name), "zone": track.zone},
            )

        except Exception as e:
            print(f"Error decoding/sending image: {e}")


# consumer_thread = threading.Thread(target=consume_messages)
# consumer_thread.daemon = True
# consumer_thread.start()


if __name__ == "__main__":
    socketio.run(app, debug=True)
