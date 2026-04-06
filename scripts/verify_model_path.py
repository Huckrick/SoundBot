#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证模型路径解析是否正确

使用方法:
    # 1. 基础验证（开发环境）
    python scripts/verify_model_path.py
    
    # 2. 模拟打包后环境（设置环境变量）
    SOUNDBOT_MODELS_PATH=/path/to/models python scripts/verify_model_path.py
    
    # 3. 验证打包后的可执行文件
    # 先构建，然后运行:
    ./dist/backend/soundbot-backend/soundbot-backend --verify-model-path
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

# 添加 backend 到路径
backend_dir = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(backend_dir))


def test_development_mode():
    """测试开发环境模式"""
    print("=" * 60)
    print("测试 1: 开发环境模式")
    print("=" * 60)
    
    import config
    
    print(f"\n1.1 模块导入时的静态路径 (CLAP_MODEL_NAME):")
    print(f"    {config.CLAP_MODEL_NAME}")
    
    print(f"\n1.2 运行时动态获取的路径 (get_clap_model_name()):")
    runtime_path = config.get_clap_model_name()
    print(f"    {runtime_path}")
    
    print(f"\n1.3 检查模型目录是否存在:")
    models_dir = config.find_models_dir_runtime()
    clap_dir = models_dir / 'clap'
    if clap_dir.exists():
        print(f"    ✅ 找到模型目录: {clap_dir}")
    else:
        print(f"    ⚠️  模型目录不存在: {clap_dir}")
        print(f"       将回退到 HuggingFace: laion/larger_clap_general")
    
    return True


def test_with_env_var():
    """测试设置环境变量后的路径解析"""
    print("\n" + "=" * 60)
    print("测试 2: 模拟打包后环境（通过环境变量）")
    print("=" * 60)
    
    # 创建临时目录模拟模型目录
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_models_dir = Path(tmpdir) / "fake_models"
        fake_clap_dir = fake_models_dir / "clap"
        fake_clap_dir.mkdir(parents=True)
        
        # 设置环境变量
        os.environ['SOUNDBOT_MODELS_PATH'] = str(fake_models_dir)
        
        # 重新导入 config 模块以读取新环境变量
        # 注意：在真实场景中，这是启动新进程时才会发生的
        import config
        
        print(f"\n2.1 设置的环境变量:")
        print(f"    SOUNDBOT_MODELS_PATH={fake_models_dir}")
        
        print(f"\n2.2 模块导入时的静态路径 (CLAP_MODEL_NAME):")
        print(f"    {config.CLAP_MODEL_NAME}")
        print(f"    (注意：这仍然是模块导入时计算的值)")
        
        print(f"\n2.3 运行时动态获取的路径 (get_clap_model_name()):")
        runtime_path = config.get_clap_model_name()
        print(f"    {runtime_path}")
        
        # 验证是否使用了环境变量
        if str(fake_clap_dir) == runtime_path:
            print(f"\n    ✅ 成功！get_clap_model_name() 正确使用了环境变量")
        else:
            print(f"\n    ❌ 失败！路径不匹配")
            print(f"       期望: {fake_clap_dir}")
            print(f"       实际: {runtime_path}")
            return False
        
        # 清理环境变量
        del os.environ['SOUNDBOT_MODELS_PATH']
        
        return True


def test_pyinstaller_simulation():
    """模拟 PyInstaller 打包后的场景"""
    print("\n" + "=" * 60)
    print("测试 3: 模拟 PyInstaller 打包后场景")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建模拟的打包后目录结构
        # 模拟: /path/to/SoundBot.app/Contents/MacOS/soundbot-backend
        exe_dir = Path(tmpdir) / "SoundBot.app" / "Contents" / "MacOS"
        exe_dir.mkdir(parents=True)
        
        # 在 Resources 中创建模型目录
        resources_dir = Path(tmpdir) / "SoundBot.app" / "Contents" / "Resources"
        models_dir = resources_dir / "models"
        clap_dir = models_dir / "clap"
        clap_dir.mkdir(parents=True)
        
        # 创建标记文件
        (clap_dir / "model.safetensors").touch()
        
        # 设置环境变量（Electron 启动时会设置）
        os.environ['SOUNDBOT_MODELS_PATH'] = str(models_dir)
        
        # 模拟 sys.frozen
        original_frozen = getattr(sys, 'frozen', None)
        sys.frozen = True
        
        # 修改 sys.executable 模拟打包后的路径
        original_executable = sys.executable
        sys.executable = str(exe_dir / "soundbot-backend")
        
        try:
            # 重新加载 config 模块
            import importlib
            import config
            importlib.reload(config)
            
            print(f"\n3.1 模拟的打包后路径结构:")
            print(f"    可执行文件: {sys.executable}")
            print(f"    模型目录: {models_dir}")
            
            print(f"\n3.2 模块导入时的静态路径:")
            print(f"    {config.CLAP_MODEL_NAME}")
            
            print(f"\n3.3 运行时动态获取的路径:")
            runtime_path = config.get_clap_model_name()
            print(f"    {runtime_path}")
            
            if str(clap_dir) == runtime_path:
                print(f"\n    ✅ 成功！正确解析了打包后的模型路径")
                return True
            else:
                print(f"\n    ❌ 失败！路径不匹配")
                return False
                
        finally:
            # 恢复
            sys.frozen = original_frozen
            sys.executable = original_executable
            if 'SOUNDBOT_MODELS_PATH' in os.environ:
                del os.environ['SOUNDBOT_MODELS_PATH']


def test_import_chain():
    """测试完整的导入链"""
    print("\n" + "=" * 60)
    print("测试 4: 验证关键模块导入链")
    print("=" * 60)
    
    tests = [
        ('config', 'get_clap_model_name'),
        ('core.embedder', 'CLIPEmbedder'),
        ('core.model_preloader', 'get_preloader'),
    ]
    
    all_ok = True
    for module_name, attr_name in tests:
        try:
            module = __import__(module_name, fromlist=[attr_name])
            attr = getattr(module, attr_name, None)
            if attr:
                print(f"  ✅ {module_name}.{attr_name}")
            else:
                print(f"  ⚠️  {module_name}.{attr_name} 不存在")
                all_ok = False
        except Exception as e:
            print(f"  ❌ {module_name}.{attr_name} 导入失败: {e}")
            all_ok = False
    
    return all_ok


def main():
    """主函数"""
    print("=" * 60)
    print("SoundBot 模型路径解析验证工具")
    print("=" * 60)
    print()
    
    results = []
    
    try:
        results.append(("开发环境模式", test_development_mode()))
    except Exception as e:
        print(f"开发环境测试失败: {e}")
        results.append(("开发环境模式", False))
    
    try:
        results.append(("环境变量模式", test_with_env_var()))
    except Exception as e:
        print(f"环境变量测试失败: {e}")
        results.append(("环境变量模式", False))
    
    try:
        results.append(("PyInstaller 模拟", test_pyinstaller_simulation()))
    except Exception as e:
        print(f"PyInstaller 模拟测试失败: {e}")
        results.append(("PyInstaller 模拟", False))
    
    try:
        results.append(("模块导入链", test_import_chain()))
    except Exception as e:
        print(f"导入链测试失败: {e}")
        results.append(("模块导入链", False))
    
    # 汇总
    print("\n" + "=" * 60)
    print("验证结果汇总")
    print("=" * 60)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"  {name:20s} - {status}")
    
    all_passed = all(r[1] for r in results)
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 所有验证通过！")
        print("\n可以安全地进行 PyInstaller 打包。")
    else:
        print("⚠️  部分验证失败，请检查上述输出。")
    print("=" * 60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
