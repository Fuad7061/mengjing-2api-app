import threading, time, json, urllib.request, urllib.error, sys, os
URL = 'http://127.0.0.1:%s/v1/images/generations' % sys.argv[1]
KEY = os.environ.get('MJ_KEY') or (sys.argv[2] if len(sys.argv) > 2 else '')
TIMEOUT = float(os.environ.get('MJ_BENCH_TIMEOUT', 180))
HDRS = {'Content-Type': 'application/json'}
if KEY:
    HDRS['Authorization'] = 'Bearer ' + KEY

def bench(n, conc):
    lat = []; cnt = [0]; lock = threading.Lock(); lock2 = threading.Lock()
    def worker():
        while True:
            with lock:
                if cnt[0] >= n: return
                cnt[0] += 1
            t = time.perf_counter()
            ok = False
            try:
                r = urllib.request.urlopen(urllib.request.Request(URL,
                    data=json.dumps({'prompt': 'p'}).encode(),
                    headers=HDRS), timeout=TIMEOUT)
                r.read(); ok = (r.status == 200)
            except Exception:
                pass
            with lock2:
                lat.append(time.perf_counter() - t)
                if not ok: lat.append(('ERR',))
    ths = [threading.Thread(target=worker) for _ in range(conc)]
    t0 = time.perf_counter(); [t.start() for t in ths]; [t.join() for t in ths]
    el = time.perf_counter() - t0
    good = sorted(x for x in lat if not isinstance(x, tuple))
    errs = len(lat) - len(good)
    p = lambda q: good[min(int(q * len(good)), len(good)-1)] * 1000
    print(f'{n} req / {conc} conc: rps={n/el:.1f}  p50={p(.5):.1f}ms  p99={p(.99):.1f}ms  errs={errs}')

bench(int(os.environ.get('MJ_BENCH_N', 200)), int(os.environ.get('MJ_BENCH_C1', 10)))
bench(int(os.environ.get('MJ_BENCH_N', 200)), int(os.environ.get('MJ_BENCH_C2', 60)))
e = 0
for _ in range(20):
    try:
        r = urllib.request.urlopen(urllib.request.Request(URL, data=b'{"prompt":""}',
            headers=HDRS))
        code = r.status
    except urllib.error.HTTPError as ex:
        code = ex.code
    if code == 400: e += 1
print(f'error-path: {e}/20 -> 400')
print('health:', urllib.request.urlopen(URL.rsplit('/v1', 1)[0] + '/health').read().decode())
