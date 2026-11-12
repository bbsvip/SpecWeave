# Cấu hình

V1 dùng `.env` làm nguồn secret và database connectivity. SearXNG, provider/model, project profiles và preferences được lưu trong PostgreSQL. Credentials của providers được Fernet mã hóa, không đưa vào project folders.

Không đưa `.env` hoặc volume database lên Git.
