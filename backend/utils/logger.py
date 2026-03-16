"""
日志工具

提供统一的日志格式和日志管理功能。
"""

import logging
import sys
from pathlib import Path
from typing import Optional
from datetime import datetime

import config


def setup_logger(
    name: str = "soundmind",
    level: int = logging.INFO,
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    设置日志记录器
    
    Args:
        name: 日志记录器名称
        level: 日志级别
        log_file: 日志文件路径，None 则只输出到控制台
        
    Returns:
        配置好的日志记录器
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # 避免重复添加 handler
    if logger.handlers:
        return logger
    
    # 日志格式
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 文件处理器
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


def get_logger(name: str = "soundmind") -> logging.Logger:
    """
    获取日志记录器
    
    Args:
        name: 日志记录器名称
        
    Returns:
        日志记录器
    """
    return logging.getLogger(name)


# 创建默认日志目录和日志记录器
def init_logger() -> logging.Logger:
    """初始化默认日志记录器"""
    log_dir = Path(config.BASE_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = log_dir / f"soundmind_{datetime.now().strftime('%Y%m%d')}.log"
    
    return setup_logger(
        name="soundmind",
        level=logging.DEBUG if config.DEBUG else logging.INFO,
        log_file=str(log_file)
    )


# 默认日志记录器
logger = init_logger()
