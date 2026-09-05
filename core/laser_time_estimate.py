import math
import re


GCODE_WORD_RE = re.compile(r"([A-Z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))", re.IGNORECASE)
LASERGRBL_RAPID_RATE_MM_PER_MINUTE = 12000.0


def estimate_gcode_file_time(gcode_file, rapid_rate=LASERGRBL_RAPID_RATE_MM_PER_MINUTE):
    try:
        with open(gcode_file, "r", encoding="utf-8", errors="replace") as file:
            return estimate_gcode_time_seconds(file.read(), rapid_rate=rapid_rate)
    except OSError:
        return None


def estimate_gcode_time_seconds(gcode, rapid_rate=LASERGRBL_RAPID_RATE_MM_PER_MINUTE):
    x_coord = 0.0
    y_coord = 0.0
    z_coord = 0.0
    wco_x = 0.0
    wco_y = 0.0
    wco_z = 0.0
    feed_rate = 0.0
    spindle_power = 0.0
    motion_mode = 0
    spindle_mode = 5
    absolute = True
    laser_seconds = 0.0
    non_laser_seconds = 0.0
    laser_distance_mm = 0.0
    non_laser_distance_mm = 0.0

    for raw_line in str(gcode or "").splitlines():
        words = _parse_gcode_words(raw_line)
        if not words:
            continue
        previous_x, previous_y, previous_z = x_coord, y_coord, z_coord
        for letter, value in words:
            if letter == "G":
                code = int(value)
                if code in {0, 1, 2, 3}:
                    motion_mode = code
                elif code == 90:
                    absolute = True
                elif code == 91:
                    absolute = False
            elif letter == "M":
                code = int(value)
                if code in {3, 4, 5}:
                    spindle_mode = code
            elif letter == "F":
                feed_rate = value
            elif letter == "S":
                spindle_power = value

        if _has_word(words, "G", 92):
            if _has_letter(words, "X"):
                wco_x = x_coord - _word_value(words, "X")
            if _has_letter(words, "Y"):
                wco_y = y_coord - _word_value(words, "Y")
            if _has_letter(words, "Z"):
                wco_z = z_coord - _word_value(words, "Z")
            continue

        x_coord = _updated_axis(words, "X", x_coord, absolute, wco_x)
        y_coord = _updated_axis(words, "Y", y_coord, absolute, wco_y)
        z_coord = _updated_axis(words, "Z", z_coord, absolute, wco_z)

        if _has_word(words, "G", 4):
            pause_seconds = _word_value(words, "P") if _has_letter(words, "P") else (_word_value(words, "S") if _has_letter(words, "S") else 0.0)
            non_laser_seconds += pause_seconds
            continue

        distance = math.hypot(x_coord - previous_x, y_coord - previous_y)
        if distance == 0:
            continue
        if motion_mode == 0:
            speed = rapid_rate
        elif motion_mode in {1, 2, 3} and feed_rate > 0:
            speed = min(feed_rate, rapid_rate)
        else:
            continue
        if motion_mode in {2, 3}:
            distance = _arc_distance(words, previous_x, previous_y, x_coord, y_coord, motion_mode) or distance
        seconds = distance / (speed / 60)
        if _laser_burning(spindle_mode, motion_mode, spindle_power):
            laser_seconds += seconds
            laser_distance_mm += distance
        else:
            non_laser_seconds += seconds
            non_laser_distance_mm += distance

    total_seconds = laser_seconds + non_laser_seconds
    payload = time_estimate_payload(total_seconds, laser_seconds, non_laser_seconds, laser_distance_mm, non_laser_distance_mm)
    payload["method"] = "lasergrbl_style_gcode_motion_estimate"
    payload["note"] = "按最终 G-code/NC 的 G0/G1、模态进给、M3/M4/M5 和 G4 暂停估算，对齐 LaserGRBL 离线 estimated time；不包含控制器加减速和人工操作时间。"
    return payload


def time_estimate_payload(total_seconds, laser_seconds, non_laser_seconds, laser_distance_mm, non_laser_distance_mm):
    return {
        "estimated_seconds": round(total_seconds, 1),
        "estimated_minutes": round(total_seconds / 60, 2),
        "laser_seconds": round(laser_seconds, 1),
        "non_laser_seconds": round(non_laser_seconds, 1),
        "laser_distance_mm": round(laser_distance_mm, 3),
        "non_laser_distance_mm": round(non_laser_distance_mm, 3),
        "method": "motion_distance_feedrate_estimate",
        "note": "估算未包含控制器加减速、停顿和人工操作时间，实际时间通常会略有差异。",
    }


def format_duration_zh(seconds):
    raw_seconds = max(0.0, float(seconds or 0))
    value = int(raw_seconds + 0.5)
    if raw_seconds > 0 and value == 0:
        value = 1
    minutes, remaining_seconds = divmod(value, 60)
    if minutes <= 0:
        return f"{remaining_seconds} 秒"
    if remaining_seconds == 0:
        return f"{minutes} 分钟"
    return f"{minutes} 分 {remaining_seconds} 秒"


def build_time_estimate_speech(estimate, prefix="预计雕刻"):
    if not isinstance(estimate, dict):
        return ""
    seconds = estimate.get("estimated_seconds") or estimate.get("total_seconds")
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return ""
    return f"{prefix} {format_duration_zh(seconds)}"


def add_time_estimate_fields(payload, gcode_file, speech_prefix="预计雕刻"):
    estimate = estimate_gcode_file_time(gcode_file)
    if not estimate:
        return payload
    payload["time_estimate"] = estimate
    speech = build_time_estimate_speech(estimate, prefix=speech_prefix)
    if speech:
        payload["speech"] = speech
    return payload


def _parse_gcode_words(line):
    line = re.sub(r"\([^)]*\)", "", line)
    line = line.split(";", 1)[0]
    return [(match.group(1).upper(), float(match.group(2))) for match in GCODE_WORD_RE.finditer(line)]


def _has_letter(words, letter):
    return any(word_letter == letter for word_letter, _ in words)


def _has_word(words, letter, value):
    return any(word_letter == letter and int(word_value) == value for word_letter, word_value in words)


def _word_value(words, letter):
    for word_letter, word_value in reversed(words):
        if word_letter == letter:
            return word_value
    return 0.0


def _updated_axis(words, letter, current, absolute, offset):
    if not _has_letter(words, letter):
        return current
    value = _word_value(words, letter)
    return value + offset if absolute else current + value


def _laser_burning(spindle_mode, motion_mode, spindle_power):
    return spindle_power > 0 and (spindle_mode == 3 or (spindle_mode == 4 and motion_mode != 0))


def _arc_distance(words, previous_x, previous_y, x_coord, y_coord, motion_mode):
    if not _has_letter(words, "I") or not _has_letter(words, "J"):
        return None
    center_x = previous_x + _word_value(words, "I")
    center_y = previous_y + _word_value(words, "J")
    radius = math.hypot(previous_x - center_x, previous_y - center_y)
    end_radius = math.hypot(x_coord - center_x, y_coord - center_y)
    if radius <= 0 or not math.isfinite(radius) or abs(end_radius - radius) > max(0.01, radius * 0.01):
        return None
    start_angle = math.atan2(previous_y - center_y, previous_x - center_x)
    end_angle = math.atan2(y_coord - center_y, x_coord - center_x)
    if motion_mode == 2:
        sweep = (start_angle - end_angle) % (2 * math.pi)
    else:
        sweep = (end_angle - start_angle) % (2 * math.pi)
    if sweep == 0:
        return None
    return radius * sweep
