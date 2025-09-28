#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直播源管理工具 - 无HTTP服务器版
主程序模块，协调各个模块的工作

主要功能：
1. 初始化所有组件（配置、日志、规则、管理器等）
2. 下载、解析、测试直播源
3. 生成M3U和TXT播放列表文件
4. 直接输出文件到Nginx服务目录
5. 提供统计信息和状态监控

修复内容：
- 修复Python模块导入路径问题
- 增强错误处理和日志记录
- 优化Nginx目录权限检查
"""

import os
import sys
import time
import asyncio
import traceback
import socket
from typing import List, Dict

# 🔧 关键修复：确保容器内Python模块导入路径正确
# 在导入自定义模块之前设置Python路径
sys.path.insert(0, '/app')  # 确保可以找到/app目录下的自定义模块

# 现在安全地导入自定义模块
from config_manager import Config, Logger
from channel_rules import ChannelRules
from source_manager import SourceManager
from stream_tester import StreamTester
from m3u_generator import M3UGenerator

def check_network_connectivity() -> bool:
    """检查网络连接性
    
    Returns:
        bool: 网络是否可用
    """
    try:
        # 尝试连接Google DNS服务器，检查基本网络连通性
        socket.create_connection(("8.8.8.8", 53), timeout=5)
        return True
    except OSError:
        return False

class LiveSourceManager:
    """直播源管理器主类（Nginx版）"""
    
    def __init__(self):
        """初始化管理器实例"""
        self.config = None
        self.logger = None
        self.channel_rules = None
        self.source_manager = None
        self.stream_tester = None
        self.start_time = None
        
    def initialize(self) -> bool:
        """初始化所有组件
        
        Returns:
            bool: 初始化是否成功
        """
        try:
            self.start_time = time.time()
            print("开始初始化直播源管理工具（Nginx版）...")
            
            # 1. 初始化配置管理器
            print("初始化配置管理器...")
            self.config = Config()
            
            # 2. 初始化日志系统
            print("初始化日志系统...")
            logger_config = self.config.get_logging_config()
            temp_logger = Logger(logger_config)
            self.logger = temp_logger.logger
            self.logger.info("配置管理器和日志系统初始化完成")
            
            # 3. 验证Nginx输出目录权限
            self.logger.info("验证Nginx输出目录权限...")
            if not self._verify_nginx_directory():
                self.logger.error("Nginx输出目录验证失败")
                return False
                
            # 4. 初始化频道规则
            self.logger.info("初始化频道规则...")
            self.channel_rules = ChannelRules()
            self.logger.info("频道规则初始化完成")
            
            # 5. 初始化源管理器
            self.logger.info("初始化源管理器...")
            self.source_manager = SourceManager(self.config, self.logger, self.channel_rules)
            self.logger.info("源管理器初始化完成")
            
            # 6. 初始化流媒体测试器
            self.logger.info("初始化流媒体测试器...")
            self.stream_tester = StreamTester(self.config, self.logger)
            self.logger.info("流媒体测试器初始化完成")
            
            initialization_time = time.time() - self.start_time
            self.logger.info(f"所有组件初始化完成，耗时 {initialization_time:.2f} 秒")
            return True
            
        except Exception as e:
            error_msg = f"初始化失败: {e}"
            print(error_msg)
            if hasattr(self, 'logger') and self.logger:
                self.logger.error(error_msg)
                self.logger.error(traceback.format_exc())
            else:
                print(traceback.format_exc())
            return False
    
    def _verify_nginx_directory(self) -> bool:
        """验证Nginx输出目录权限和可访问性
        
        Returns:
            bool: 目录是否可用
        """
        try:
            output_dir = self.config.get_output_params()['output_dir']
            self.logger.info(f"验证Nginx输出目录: {output_dir}")
            
            # 创建目录（如果不存在）
            os.makedirs(output_dir, exist_ok=True)
            
            # 检查目录权限
            if not os.access(output_dir, os.W_OK):
                self.logger.warning(f"输出目录不可写，尝试修复权限: {output_dir}")
                try:
                    os.chmod(output_dir, 0o755)
                    self.logger.info("目录权限修复成功")
                except Exception as e:
                    self.logger.error(f"目录权限修复失败: {e}")
                    return False
            
            # 验证Nginx用户访问权限
            test_file = os.path.join(output_dir, ".permission_test")
            try:
                with open(test_file, 'w') as f:
                    f.write("test")
                os.remove(test_file)
                self.logger.info("✓ Nginx目录权限验证通过")
                return True
            except Exception as e:
                self.logger.error(f"✗ Nginx目录权限验证失败: {e}")
                return False
                
        except Exception as e:
            self.logger.error(f"验证Nginx目录时发生错误: {e}")
            return False
    
    def ensure_output_directory(self) -> bool:
        """确保输出目录存在并可访问
        
        Returns:
            bool: 目录准备是否成功
        """
        try:
            output_dir = self.config.get_output_params()['output_dir']
            self.logger.info(f"检查输出目录: {output_dir}")
            
            # 创建目录（如果不存在）
            os.makedirs(output_dir, exist_ok=True)
            
            # 检查目录权限
            if not os.access(output_dir, os.W_OK):
                self.logger.error(f"输出目录不可写: {output_dir}")
                return False
            
            # 创建默认文件，确保Nginx启动后立即有内容可服务
            self._create_default_files(output_dir)
            
            self.logger.info(f"输出目录准备完成: {output_dir}")
            return True
            
        except Exception as e:
            self.logger.error(f"准备输出目录失败: {e}")
            return False

    def _create_default_files(self, output_dir: str):
        """创建默认文件，确保Nginx启动后立即有内容"""
        try:
            # 创建默认的M3U文件
            base_filename = self.config.get_output_params()['filename'].replace('.m3u', '')
            default_m3u_path = os.path.join(output_dir, f"{base_filename}.m3u")
            
            if not os.path.exists(default_m3u_path):
                default_content = """#EXTM3U
#EXTINF:-1 tvg-id="default" tvg-name="默认频道" group-title="系统消息",默认频道
# 直播源管理工具正在处理中，请稍后刷新...
https://example.com/default"""
                
                with open(default_m3u_path, 'w', encoding='utf-8') as f:
                    f.write(default_content)
                self.logger.info(f"创建默认M3U文件: {default_m3u_path}")
            
            # 创建默认的TXT文件
            default_txt_path = os.path.join(output_dir, f"{base_filename}.txt")
            if not os.path.exists(default_txt_path):
                default_txt_content = """# 直播源管理工具
# 正在处理直播源，请稍后刷新...
默认频道,https://example.com/default"""
                
                with open(default_txt_path, 'w', encoding='utf-8') as f:
                    f.write(default_txt_content)
                self.logger.info(f"创建默认TXT文件: {default_txt_path}")
                
            # 确保文件权限正确
            os.chmod(default_m3u_path, 0o644)
            os.chmod(default_txt_path, 0o644)
                
        except Exception as e:
            self.logger.warning(f"创建默认文件失败: {e}")

    def _list_output_files(self, output_dir: str):
        """列出输出目录中的文件"""
        try:
            if os.path.exists(output_dir):
                files = os.listdir(output_dir)
                self.logger.info("输出目录文件列表:")
                for file in sorted(files):
                    file_path = os.path.join(output_dir, file)
                    if os.path.isfile(file_path):
                        size = os.path.getsize(file_path)
                        permissions = oct(os.stat(file_path).st_mode)[-3:]
                        self.logger.info(f"  {file} ({size} 字节, 权限: {permissions})")
        except Exception as e:
            self.logger.warning(f"列出输出文件失败: {e}")
    
    async def process_sources(self) -> bool:
        """处理直播源的完整流程
        
        流程步骤：
        1. 下载源文件
        2. 解析源文件
        3. 测试流媒体源
        4. 生成播放列表文件
        5. 输出到Nginx目录
        
        Returns:
            bool: 处理流程是否成功完成
        """
        if not all([self.source_manager, self.stream_tester]):
            self.logger.error("必要的组件未正确初始化，无法处理源文件")
            return False
        
        try:
            self.logger.info("开始直播源处理流程...")
            process_start_time = time.time()
            
            # 步骤1: 下载所有源文件
            self.logger.info("=== 步骤1: 下载源文件 ===")
            downloaded_files = await self.source_manager.download_all_sources()
            
            if not downloaded_files:
                self.logger.warning("没有成功下载任何源文件，尝试使用缓存文件继续处理")
            
            # 步骤2: 解析所有源文件
            self.logger.info("=== 步骤2: 解析源文件 ===")
            sources = self.source_manager.parse_all_files()
            
            if not sources:
                self.logger.error("没有解析到任何有效的直播源，处理流程终止")
                return False
            
            self.logger.info(f"成功解析 {len(sources)} 个直播源")
            
            # 步骤3: 测试所有流媒体源
            self.logger.info("=== 步骤3: 测试流媒体源 ===")
            test_results = self.stream_tester.test_all_sources(sources)
            
            # 步骤4: 分离有效源和合格源
            valid_sources = [s for s in test_results if s.get('status') == 'success']
            qualified_sources = [s for s in test_results if s.get('is_qualified')]
            
            self.logger.info(f"测试完成: {len(valid_sources)} 个有效源, {len(qualified_sources)} 个合格源")
            
            # 步骤5: 生成播放列表文件
            self.logger.info("=== 步骤4: 生成播放列表文件 ===")
            generator = M3UGenerator(self.config, self.logger)
            
            # 生成主播放列表文件（包含所有有效源）
            if valid_sources:
                success = self._generate_playlist_files(generator, valid_sources, "")
                if not success:
                    self.logger.error("生成主播放列表文件失败")
            else:
                self.logger.warning("没有有效源，跳过主播放列表文件生成")
            
            # 生成合格播放列表文件（仅包含合格源）
            if qualified_sources:
                success = self._generate_playlist_files(generator, qualified_sources, "qualified_")
                if not success:
                    self.logger.error("生成合格播放列表文件失败")
            else:
                self.logger.warning("没有合格源，跳过合格播放列表文件生成")
            
            # 步骤6: 输出统计信息
            self.logger.info("=== 步骤5: 生成统计信息 ===")
            self.output_statistics(valid_sources, qualified_sources)
            
            process_time = time.time() - process_start_time
            self.logger.info(f"直播源处理流程完成，总耗时 {process_time:.2f} 秒")
            
            return True
            
        except Exception as e:
            self.logger.error(f"处理直播源过程中发生错误: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    def _generate_playlist_files(self, generator: M3UGenerator, sources: List[Dict], prefix: str = "") -> bool:
        """生成播放列表文件 - 直接写入输出目录"""
        try:
            # 生成M3U文件内容
            m3u_content = generator.generate_m3u(sources)
            
            # 生成TXT文件内容
            txt_content = generator.generate_txt(sources)
            
            # 获取基础文件名
            base_filename = self.config.get_output_params()['filename'].replace('.m3u', '')
            
            # 直接写入到输出目录（原子操作）
            output_dir = self.config.get_output_params()['output_dir']
            
            # 确保输出目录存在
            os.makedirs(output_dir, exist_ok=True)
            
            # 原子写入M3U文件
            m3u_filename = f"{prefix}{base_filename}.m3u"
            m3u_final_path = os.path.join(output_dir, m3u_filename)
            m3u_temp_path = f"{m3u_final_path}.tmp"
            
            with open(m3u_temp_path, 'w', encoding='utf-8') as f:
                f.write(m3u_content)
            os.replace(m3u_temp_path, m3u_final_path)
            
            # 原子写入TXT文件
            txt_filename = f"{prefix}{base_filename}.txt"
            txt_final_path = os.path.join(output_dir, txt_filename)
            txt_temp_path = f"{txt_final_path}.tmp"
            
            with open(txt_temp_path, 'w', encoding='utf-8') as f:
                f.write(txt_content)
            os.replace(txt_temp_path, txt_final_path)
            
            # 记录文件信息
            m3u_size = os.path.getsize(m3u_final_path)
            txt_size = os.path.getsize(txt_final_path)
            
            self.logger.info(f"成功生成 {prefix}播放列表文件:")
            self.logger.info(f"  {m3u_filename} ({m3u_size} 字节)")
            self.logger.info(f"  {txt_filename} ({txt_size} 字节)")
            
            # 设置文件权限，确保Nginx可以读取
            os.chmod(m3u_final_path, 0o644)
            os.chmod(txt_final_path, 0o644)
            
            return True
                
        except Exception as e:
            self.logger.error(f"生成播放列表文件时发生错误: {e}")
            self.logger.error(traceback.format_exc())
            return False
    
    def output_statistics(self, valid_sources: List[Dict], qualified_sources: List[Dict]):
        """输出详细的统计信息
        
        Args:
            valid_sources: 有效源列表
            qualified_sources: 合格源列表
        """
        self.logger.info("=" * 50)
        self.logger.info("直播源处理统计报告")
        self.logger.info("=" * 50)
        
        # 基本统计
        self.logger.info(f"有效源总数: {len(valid_sources)}")
        self.logger.info(f"合格源总数: {len(qualified_sources)}")
        self.logger.info(f"合格率: {len(qualified_sources)/len(valid_sources)*100:.1f}%" if valid_sources else "N/A")
        
        # 按来源类型统计
        self.logger.info("-" * 30)
        self.logger.info("按来源类型统计:")
        source_types = {}
        for source in valid_sources:
            src_type = source.get('source_type', 'unknown')
            source_types[src_type] = source_types.get(src_type, 0) + 1
        
        for src_type, count in source_types.items():
            qualified_count = len([s for s in qualified_sources if s.get('source_type') == src_type])
            self.logger.info(f"  {src_type}: {count} 有效, {qualified_count} 合格")
        
        # 按分类统计
        self.logger.info("-" * 30)
        self.logger.info("按频道分类统计:")
        categories = {}
        for source in valid_sources:
            category = source.get('category', 'unknown')
            categories[category] = categories.get(category, 0) + 1
        
        # 按数量排序
        sorted_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)
        for category, count in sorted_categories:
            qualified_count = len([s for s in qualified_sources if s.get('category') == category])
            self.logger.info(f"  {category}: {count} 有效, {qualified_count} 合格")
        
        # 文件统计
        self.logger.info("-" * 30)
        self.logger.info("文件统计信息:")
        base_filename = self.config.get_output_params()['filename'].replace('.m3u', '')
        output_dir = self.config.get_output_params()['output_dir']
        
        files_to_check = [
            f"{base_filename}.m3u",
            f"{base_filename}.txt",
            f"qualified_{base_filename}.m3u",
            f"qualified_{base_filename}.txt"
        ]
        
        for filename in files_to_check:
            filepath = os.path.join(output_dir, filename)
            if os.path.exists(filepath):
                try:
                    size = os.path.getsize(filepath)
                    # 计算频道数量
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    if filename.endswith('.m3u'):
                        channel_count = content.count('#EXTINF:')
                    else:
                        lines = [line.strip() for line in content.split('\n') 
                                if line.strip() and not line.startswith('#')]
                        channel_count = len(lines)
                    
                    self.logger.info(f"  {filename}: {channel_count} 个频道, {size} 字节")
                except Exception as e:
                    self.logger.warning(f"  {filename}: 读取失败 - {e}")
            else:
                self.logger.warning(f"  {filename}: 文件不存在")
        
        self.logger.info("=" * 50)

    def _output_access_info(self):
        """输出访问信息"""
        output_dir = self.config.get_output_params()['output_dir']
        base_filename = self.config.get_output_params()['filename'].replace('.m3u', '')
        
        # 获取容器IP（简化显示）
        container_ip = "容器IP"
        
        self.logger.info("=" * 50)
        self.logger.info("文件访问地址 (通过Nginx):")
        self.logger.info(f"主播放列表: http://{container_ip}/{base_filename}.m3u")
        self.logger.info(f"合格播放列表: http://{container_ip}/qualified_{base_filename}.m3u")
        self.logger.info(f"主文本列表: http://{container_ip}/{base_filename}.txt")
        self.logger.info(f"合格文本列表: http://{container_ip}/qualified_{base_filename}.txt")
        self.logger.info("=" * 50)
    
    def run(self) -> bool:
        """运行主程序 - Nginx版"""
        # 首先准备输出目录
        self.logger.info("第一步：准备输出目录...")
        output_success = self.ensure_output_directory()
        
        if not output_success:
            self.logger.error("输出目录准备失败")
            return False
        
        # 检查网络连接
        if not check_network_connectivity():
            self.logger.warning("网络连接不可用，将使用本地源和缓存")
        
        # 运行主处理流程
        self.logger.info("第二步：开始处理直播源...")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            process_success = loop.run_until_complete(self.process_sources())
            
            if process_success:
                total_time = time.time() - self.start_time
                self.logger.info(f"直播源处理完成，总耗时 {total_time:.2f} 秒")
                
                # 输出访问信息
                self._output_access_info()
                
                # 列出最终文件
                output_dir = self.config.get_output_params()['output_dir']
                self._list_output_files(output_dir)
                
                return True
            else:
                self.logger.error("直播源处理失败")
                return False
                
        except Exception as e:
            self.logger.error(f"主程序运行失败: {e}")
            self.logger.error(traceback.format_exc())
            return False

def main():
    """主函数入口点"""
    print("直播源管理工具（Nginx版）启动中...")
    
    # 创建管理器实例
    manager = LiveSourceManager()
    
    # 初始化所有组件
    if not manager.initialize():
        print("初始化失败，程序退出")
        return 1
    
    # 运行主程序
    success = manager.run()
    
    if success:
        print("程序执行成功")
        return 0
    else:
        print("程序执行失败")
        return 1

if __name__ == "__main__":
    # 设置默认编码
    import locale
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except:
        pass  # 如果设置失败，继续执行
    
    # 运行主程序
    sys.exit(main())