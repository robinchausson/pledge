FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY client_edge.py edge_transcoder.py manager_edge.py ./
COPY worker/ ./worker/
# L'ajout du -u est primordial pour voir les erreurs en direct !
CMD ["python", "-u", "client_edge.py"]