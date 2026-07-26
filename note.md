brett-m1@brett-m1s-Mac-mini mini-cloud % make -C infra up
docker compose up -d
[+] up 5/5
 ✔ Container mini-cloud-infra-grafana-1    Running                        0.0s
 ✔ Container mini-cloud-infra-postgres-1   Running                        0.0s
 ✔ Container mini-cloud-infra-minio-1      Running                        0.0s
 ✔ Container mini-cloud-infra-loki-1       Running                        0.0s
 ✔ Container mini-cloud-infra-prometheus-1 Running                        0.0s
Grafana → http://localhost:3000   MinIO console → http://localhost:9001



brett-m1@brett-m1s-Mac-mini mini-cloud % make -C infra ps
docker compose ps
NAME                            IMAGE                                      COMMAND                  SERVICE      CREATED        STATUS                  PORTS
mini-cloud-infra-grafana-1      grafana/grafana:11.2.0                     "/run.sh"                grafana      21 hours ago   Up 21 hours (healthy)   127.0.0.1:3000->3000/tcp
mini-cloud-infra-loki-1         grafana/loki:3.1.1                         "/usr/bin/loki -conf…"   loki         21 hours ago   Up 21 hours (healthy)   127.0.0.1:3100->3100/tcp
mini-cloud-infra-minio-1        minio/minio:RELEASE.2024-09-13T20-26-02Z   "/usr/bin/docker-ent…"   minio        21 hours ago   Up 21 hours (healthy)   127.0.0.1:9000-9001->9000-9001/tcp
mini-cloud-infra-postgres-1     postgres:16.4-alpine                       "docker-entrypoint.s…"   postgres     21 hours ago   Up 21 hours (healthy)   127.0.0.1:5432->5432/tcp
mini-cloud-infra-prometheus-1   prom/prometheus:v2.54.1                    "/bin/prometheus --c…"   prometheus   21 hours ago   Up 21 hours (healthy)   127.0.0.1:9090->9090/tcp



Q1. They should not use common develop port. it should be another rare ports. 