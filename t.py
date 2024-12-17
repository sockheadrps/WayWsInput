import time
from pynput.mouse import Listener as MouseListener
from queue import Queue

# Event queue to store mouse events
event_queue = Queue()

# This function handles mouse input capture and suppression of clicks


def capture_mouse_input():
    def on_move(x, y):
        # Broadcast mouse position to the WebSocket clients
        event = {
            "event": "mouse",
            "data": {"x": x, "y": y}
        }
        event_queue.put(event)

    def on_click(x, y, button, pressed):
        # Capture mouse click event but prevent it from propagating to the OS
        if pressed:
            # Send the click event to the WebSocket but suppress it
            event = {
                "event": "mouse_click",
                "data": {"x": x, "y": y, "button": str(button), "pressed": pressed}
            }
            event_queue.put(event)

        # Do not return False, so the listener keeps running
        # This prevents the click event from affecting the OS, but the listener still continues

    with MouseListener(on_move=on_move, on_click=on_click) as listener:
        print("Mouse listener started")
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("Mouse listener stopped")
        finally:
            print("Mouse listener stopped")
            listener.join()  # Keep the listener running indefinitely


capture_mouse_input()
