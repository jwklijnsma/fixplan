FROM python
RUN apt-get update && apt-get install -y curl
RUN curl -fsSL https://get.example.com/install.sh | sh
ENV API_KEY=s3cr3tvalue123456
USER root
CMD ["python", "app.py"]
