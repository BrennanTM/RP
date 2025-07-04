from flask import Flask, redirect, request
import requests

app = Flask(__name__)
STREAMLIT_URL = "http://localhost:8080"

@app.route('/')
@app.route('/<path:path>')
def proxy_all(path=''):
    url = f"{STREAMLIT_URL}/{path}"
    resp = requests.get(url, params=request.args)
    return resp.content, resp.status_code, resp.headers.items()

if __name__ == '__main__':
    print("Starting simple proxy on port 8082")
    print("Access at: http://localhost:8082")
    app.run(host='0.0.0.0', port=8082)
