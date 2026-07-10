import logging
import sys

from app.config.config import config

logger = logging.getLogger("AiditorBackend")

log_level_name = config["LOG_LEVEL"]
log_level = getattr(logging, log_level_name, logging.INFO)
logger.setLevel(log_level)
logger.debug(f"Logger initialized with level: {log_level_name}")

stream_handler = logging.StreamHandler(sys.stdout)
#"%(asctime)s - %(levelname)s - %(name)s - %(filename)s:%(lineno)d - %(message)s"
formatter = logging.Formatter(
    "%(filename)s:%(lineno)d - %(message)s"
)

stream_handler.setFormatter(formatter)

if logger.hasHandlers():
   logger.handlers.clear()
logger.addHandler(stream_handler)