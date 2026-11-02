"""Start the web UI:  python app.py  (then open http://127.0.0.1:5000)"""
import os

from webapp.app import create_app

if __name__ == "__main__":
    create_app().run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False,
                     threaded=True)
