#!/usr/bin/env bash
# 每日备份有状态数据（运动文件 + uploads + charts + chroma_db + knowledge）
# 全部位于 /opt/marathon-agent/data，与 docker-compose 挂载一致
set -e

SRC=/opt/marathon-agent/data
BACKUP_DIR=/opt/backups/marathon
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

tar czf "$BACKUP_DIR/data_$DATE.tar.gz" -C "$SRC" .

# （可选）同步到对象存储，取消注释并安装配置 coscli / aws cli
# coscli cp "$BACKUP_DIR/data_$DATE.tar.gz" cos://your-bucket/marathon-backup/

# 仅保留最近 7 天
find "$BACKUP_DIR" -name 'data_*.tar.gz' -mtime +7 -delete

echo "backup done: $DATE"
