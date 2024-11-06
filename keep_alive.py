from flask import Flask
from threading import Thread
import os

app = Flask('')

@app.route('/')
def home():
    return 'Le bot est en ligne!'

def run():
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 10000)))

def keep_alive():
    t = Thread(target=run)
    t.start()