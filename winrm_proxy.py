from flask import Flask, request, jsonify
import winrm
import base64

app = Flask(__name__)

HOSTS = {
    #'machine1': 'YOUR IP',
    #'machine 1': '',
    #'machine2': 'YOUR IP',
    #'machine 2': 'YOUR IP',
    #'192.168.0.20': '#YOUR IP',
    #'192.168.0.252': 'YOUR IP'
}

@app.route('/run', methods=['POST'])
def run():
    data = request.json
    host = HOSTS.get(data.get('target_host', '').lower().strip())
    if not host:
        return jsonify({'success': False, 'message': f'Unknown host: {data.get("target_host")}'}), 400

    # ✅ Decode base64 command here, inside the function
    encoded = data.get('command', '')
    try:
        command = base64.b64decode(encoded).decode('utf-16-le')
    except Exception as e:
        return jsonify({'success': False, 'message': f'Invalid encoded command: {str(e)}'}), 400

    try:
        s = winrm.Session(f'http://{host}:5985/wsman', auth=('Administrateur', '15089'), transport='basic')
        r = s.run_ps(command)
        out = r.std_out.decode('utf-8', 'replace').strip()
        err = r.std_err.decode('utf-8', 'replace').strip()
        ok = r.status_code == 0
        return jsonify({'success': ok, 'stdout': out, 'stderr': err, 'exit_code': r.status_code})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
