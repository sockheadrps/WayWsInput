import asyncio
import threading
import multiprocessing
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import time
import logging
import pyautogui
import signal
import json
from queue import Queue
import uvicorn
from PIL import ImageTk, Image, ImageDraw
import tkinter as tk
import keyboard

multiprocessing.set_start_method("spawn", force=True)

DEBUG = True
LOG_TO_FILE = False

if LOG_TO_FILE:
    handlers = [logging.FileHandler("app.log"), logging.StreamHandler()]
else:
    handlers = [logging.StreamHandler()]

if DEBUG:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(threadName)s - %(levelname)s - %(message)s",
                        handlers=handlers)
    logger = logging.getLogger()
else:
    logger = logging.getLogger()
    logger.addHandler(logging.NullHandler())

app = FastAPI()
connected_clients = []
stop_event = threading.Event()
event_queue = Queue()
keyboard_event_queue = Queue()

capture_active = False
screenshot_open = False
screenshot_process = None
screenshot_queue = multiprocessing.Queue()
keyboard_hook = None  # To store the hook


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    if DEBUG:
        logger.info(f"New connection from {websocket.client.host}")
    try:
        while True:
            message = await websocket.receive_text()
            await websocket.send_text(f"Message received: {message}")
    except WebSocketDisconnect:
        if DEBUG:
            logger.info(f"Client {websocket.client.host} disconnected")
        connected_clients.remove(websocket)


def capture_keyboard_input():
    global capture_active, stop_event
    held_keys = set()
    active_modifiers = set()
    special_keys = {"ctrl", "shift", "alt", "left windows", "super"}
    key_repeat_threads = {}
    stop_repeat_flags = {}
    in_combo = False

    if DEBUG:
        logger.info("Keyboard listener started")

    def start_key_repeat(key):
        """Repeatedly send key-down events or combo events after delay."""
        start_time = time.time()
        initial_delay = 0.5  # Delay before repeat starts
        repeat_interval = 0.1  # Interval between repeats

        while key in held_keys and not stop_event.is_set():
            if stop_repeat_flags.get(key, False):
                break

            if time.time() - start_time >= initial_delay:
                # Handle combo repeat if modifiers are active
                if active_modifiers:
                    combo_event = "+".join(sorted(active_modifiers | {key}))
                    keyboard_event_queue.put(
                        {"event": "combo_repeat", "data": {"keys": combo_event}}
                    )
                    if DEBUG:
                        logger.info(f"Repeated Combo Event: {combo_event}")
                        logger.info(f"Active Modifiers: {active_modifiers}")
                else:  # Normal key repeat
                    keyboard_event_queue.put(
                        {"event": "keystroke_repeat", "data": {"key": key}}
                    )
                    if DEBUG:
                        logger.info(f"Repeated Key Press: {key}")
                time.sleep(repeat_interval)

    def stop_key_repeat(key):
        """Stop key repeat logic for a specific key."""
        if key in special_keys:
            return
        if key in stop_repeat_flags:
            stop_repeat_flags[key] = True
        if key in key_repeat_threads:
            key_repeat_threads[key].join()
            del key_repeat_threads[key]
            del stop_repeat_flags[key]
        else:
            logger.warning(f"Key repeat thread not found for {key}")

    def keyboard_event_handler(event):
        nonlocal in_combo

        # Key-down event
        if event.event_type == "down":
            if event.name in held_keys:
                return  # Avoid duplicate key-down events
            held_keys.add(event.name)

            if event.name in special_keys:
                active_modifiers.add(event.name)
                in_combo = True
            else:
                # If modifiers are active, treat as a combo
                if active_modifiers:
                    combo_event = "+".join(sorted(active_modifiers | {event.name}))
                    keyboard_event_queue.put(
                        {"event": "combo", "data": {"keys": combo_event}}
                    )
                    if DEBUG:
                        logger.info(f"Combo Event: {combo_event}")
                else:
                    keyboard_event_queue.put(
                        {"event": "keystroke", "data": {"key": event.name}}
                    )
                    if DEBUG:
                        logger.info(f"Key Pressed: {event.name}")

                # Start key repeat logic
                stop_repeat_flags[event.name] = False
                key_repeat_threads[event.name] = threading.Thread(
                    target=start_key_repeat, args=(event.name,)
                )
                key_repeat_threads[event.name].daemon = True
                key_repeat_threads[event.name].start()

            # Special action: Ctrl + Space + Right
            if event.name == "right" and "ctrl" in active_modifiers and "space" in active_modifiers:
                if screenshot_open:
                    logger.info("Ctrl + Space + Right pressed. Closing screenshot.")
                    close_screenshot()
                else:
                    logger.info("Ctrl + Space + Right pressed. Taking screenshot.")
                    take_screenshot()
                capture_active = not capture_active
                logger.info(f"Capture active: {capture_active}")

        # Key-up event
        elif event.event_type == "up":
            stop_key_repeat(event.name)
            held_keys.discard(event.name)

            if event.name in active_modifiers:
                active_modifiers.discard(event.name)

            # End combo if no modifiers are active
            if in_combo and not active_modifiers:
                in_combo = False

            # Send key-up event for non-special keys if not in combo
            if not in_combo and event.name not in special_keys:
                keyboard_event_queue.put(
                    {"event": "keystroke_up", "data": {"key": event.name}}
                )
                if DEBUG:
                    logger.info(f"Key Released: {event.name}")

    global keyboard_hook
    keyboard_hook = keyboard.hook(keyboard_event_handler)
    keyboard.wait("esc")


def cleanup_key_repeat_threads():
    """Stop all key repeat threads."""
    logger.info("Stopping all key repeat threads...")
    for thread in threading.enumerate():
        if thread.name.startswith("Thread-"):
            thread.join(timeout=1.0)


def cleanup_keyboard_hook():
    """Unregister the keyboard hook."""
    global keyboard_hook
    if keyboard_hook is not None:
        logger.info("Unregistering keyboard hook...")
        keyboard.unhook(keyboard_hook)
        keyboard_hook = None


def signal_handler(signum, frame):
    """Handle termination signals."""
    logger.info("Received termination signal. Stopping services...")
    stop_event.set()
    cleanup_keyboard_hook()
    cleanup_key_repeat_threads()


def register_keyboard_hook(handler):
    global keyboard_hook
    if keyboard_hook is None:
        if DEBUG:
            logger.info("Registering keyboard hook.")
        keyboard_hook = keyboard.hook(handler)


def unregister_keyboard_hook():
    global keyboard_hook
    if keyboard_hook is not None:
        if DEBUG:
            logger.info("Unregistering keyboard hook.")
        keyboard.unhook(keyboard_hook)
        keyboard_hook = None


def capture_mouse_input():
    if DEBUG:
        logger.info("Mouse listener started")
    while not stop_event.is_set():
        if capture_active:
            x, y = pyautogui.position()
            event = {"event": "mouse", "data": {"x": x, "y": y}}
            # event_queue.put(event)
        time.sleep(0.1)


async def broadcast_events():
    while not stop_event.is_set():
        while not keyboard_event_queue.empty():
            event = keyboard_event_queue.get()
            if DEBUG:
                logger.info(f"Keyboard Event: {event}")
            for websocket in connected_clients[:]:
                try:
                    await websocket.send_text(json.dumps(event))
                except WebSocketDisconnect:
                    connected_clients.remove(websocket)
        while not event_queue.empty():
            event = event_queue.get()
            if DEBUG:
                logger.info(f"Mouse Event: {event}")
            for websocket in connected_clients[:]:
                try:
                    await websocket.send_text(json.dumps(event))
                except WebSocketDisconnect:
                    connected_clients.remove(websocket)
        await asyncio.sleep(0.1)


def draw_border(image):
    border_color = (255, 165, 0)
    border_width = 10
    width, height = image.size
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, border_width], fill=border_color)
    draw.rectangle([0, 0, border_width, height], fill=border_color)
    draw.rectangle([0, height - border_width, width, height],
                   fill=border_color)
    draw.rectangle([width - border_width, 0, width, height], fill=border_color)
    return image


def take_screenshot():
    global screenshot_open, screenshot_process
    screenshot = pyautogui.screenshot()
    screenshot_with_border = draw_border(screenshot)
    screenshot_path = "screenshot_with_border.png"
    screenshot_with_border.save(screenshot_path)
    screenshot_process = multiprocessing.Process(
        target=show_fullscreen_image, args=(screenshot_queue, screenshot_path))
    screenshot_process.start()
    screenshot_open = True
    if DEBUG:
        logger.info("Screenshot process started.")


def show_fullscreen_image(queue, image_path):
    root = tk.Tk()
    root.attributes('-fullscreen', True)
    root.configure(bg='black')
    img = Image.open(image_path)
    img = ImageTk.PhotoImage(img)
    label = tk.Label(root, image=img)
    label.image = img
    label.pack()
    root.bind("<Escape>", lambda e: queue.put("close"))

    def check_queue():
        try:
            message = queue.get_nowait()
            if message == "close":
                root.destroy()
        except:
            pass
        root.after(100, check_queue)

    check_queue()
    root.mainloop()


def close_screenshot():
    global screenshot_open, screenshot_process
    if screenshot_process and screenshot_process.is_alive():
        screenshot_queue.put("close")
        screenshot_process.join(timeout=2)
    screenshot_open = False


def signal_handler(signum, frame):
    stop_event.set()


async def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    capture_thread = threading.Thread(
        target=capture_keyboard_input, name="InputCapture")
    capture_thread.daemon = True
    capture_thread.start()

    mouse_capture_thread = threading.Thread(
        target=capture_mouse_input, name="MouseCapture")
    mouse_capture_thread.daemon = True
    mouse_capture_thread.start()

    broadcast_task = asyncio.create_task(broadcast_events())

    try:
        config = uvicorn.Config(app, host="192.168.1.135", port=8000)
        server = uvicorn.Server(config)
        await server.serve()
    except KeyboardInterrupt:
        logger.info("Received Ctrl+C. Stopping services...")
        stop_event.set()
    finally:
        logger.info("Shutting down services...")
        stop_event.set()
        broadcast_task.cancel()

        cleanup_keyboard_hook()
        cleanup_key_repeat_threads()

        if DEBUG:
            logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
