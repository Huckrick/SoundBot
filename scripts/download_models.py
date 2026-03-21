#!/usr/bin/env python3
"""
下载 AI 模型脚本 / Download AI Model Script
用于 CI/CD 或首次设置时下载模型文件
Used for CI/CD or initial setup to download model files
"""

import os
import sys
from pathlib import Path

# 设置 UTF-8 编码（Windows 兼容）
# Set UTF-8 encoding for Windows compatibility
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# 添加 backend 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

def download_clap_model():
    """下载 CLAP 模型到本地 models 目录 / Download CLAP model to local models directory"""
    
    # 先安装依赖 / Install dependencies first
    print("Installing transformers library...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "transformers", "torch", "torchaudio"])
    
    from transformers import ClapModel, ClapProcessor
    
    model_name = "laion/larger_clap_general"
    models_dir = Path(__file__).parent.parent / "models" / "clap"
    
    print(f"Downloading CLAP model to {models_dir}...")
    print(f"Model: {model_name}")
    
    # 下载模型和处理器
    model = ClapModel.from_pretrained(model_name)
    processor = ClapProcessor.from_pretrained(model_name)
    
    # 保存到本地
    models_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(models_dir)
    processor.save_pretrained(models_dir)
    
    print("[OK] Model download completed!")
    size_mb = sum(f.stat().st_size for f in models_dir.rglob('*') if f.is_file()) / 1024 / 1024
    print(f"Size: {size_mb:.1f} MB")
    
    return models_dir

if __name__ == "__main__":
    try:
        download_clap_model()
    except Exception as e:
        print(f"[ERROR] Download failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
