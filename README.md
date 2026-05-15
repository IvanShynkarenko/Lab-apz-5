## Як запустити

Один раз з кореня проєкту:
```bash
cd /шлях/до/APZ
python3 -m venv .venv && source .venv/bin/activate
pip install -r facade-service/requirements.txt -r logging-service/requirements.txt -r messages-service/requirements.txt
```

**Термінал 1** — logging:
```bash
cd /шлях/до/APZ
export PYTHONPATH="${PWD}"
python logging-service/main.py
```

**Термінал 2** — messages:
```bash
cd /шлях/до/APZ
python messages-service/main.py
```

**Термінал 3** — facade:
```bash
cd /шлях/до/APZ
python facade-service/main.py
```


```bash
curl -X POST http://localhost:50050/ -H "Content-Type: application/json" -d '{"msg": "hello"}'
curl -X POST http://localhost:50050/ -H "Content-Type: application/json" -d '{"msg": "one more hello"}'
curl http://localhost:50050/
```
