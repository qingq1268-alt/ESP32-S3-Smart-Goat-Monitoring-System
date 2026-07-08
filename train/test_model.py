"""
test_model.py
连接 ESP32-S3 串口读取启动日志，然后通过 UDP 发送 TEST 命令触发嵌入式测试窗口推理。
同时持续输出串口日志以观察推理结果。

用法:
    python test_model.py [COM端口] [--udp]
"""

import sys
import time
import socket
import threading
import serial

COM_PORT = sys.argv[1] if len(sys.argv) > 1 else "COM3"
BAUD = 115200
ESP_IP = "192.168.4.1"
CONTROL_PORT = 6006


def serial_reader(ser, stop_event):
    """持续读取串口输出"""
    while not stop_event.is_set():
        try:
            line = ser.readline()
            if line:
                try:
                    print(line.decode("utf-8", errors="replace").rstrip())
                except:
                    print(line)
        except serial.SerialException:
            break
        except Exception as e:
            print(f"[serial error] {e}")
            break


def send_test_command():
    """通过 UDP 发送 TEST 命令"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(b"TEST", (ESP_IP, CONTROL_PORT))
        print(f"\n[SENT] TEST command to {ESP_IP}:{CONTROL_PORT}\n")
    except Exception as e:
        print(f"\n[ERROR] Failed to send TEST: {e}\n")
    finally:
        sock.close()


def main():
    print(f"Opening {COM_PORT} at {BAUD} baud...")
    ser = serial.Serial(COM_PORT, BAUD, timeout=0.5)

    stop_event = threading.Event()
    reader_thread = threading.Thread(target=serial_reader, args=(ser, stop_event), daemon=True)
    reader_thread.start()

    print("Monitoring serial output. Wait for ESP32 to boot...")
    print("Press Enter to send TEST command, 'q' to quit.\n")

    try:
        while True:
            user_input = input()
            if user_input.strip().lower() == 'q':
                break
            elif user_input.strip().upper() == 'START':
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(b"START", (ESP_IP, CONTROL_PORT))
                sock.close()
                print("[SENT] START")
            elif user_input.strip().upper() == 'PAUSE':
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.sendto(b"PAUSE", (ESP_IP, CONTROL_PORT))
                sock.close()
                print("[SENT] PAUSE")
            else:
                # Default: send TEST
                send_test_command()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        ser.close()
        print("\nDone.")


if __name__ == "__main__":
    main()
