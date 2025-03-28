## Docker run
docker build -t chattutor .
docker run -p 5000:5000 chattutor


## Chroma Deployment [GCP]:

uvicorn chromadb.app:app --host 0.0.0.0 --port 8000