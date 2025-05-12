import tkinter as tk
from tkinter import filedialog
import ctypes
from bs4 import BeautifulSoup
import os
import icoextract
from urllib.parse import urlparse
import asyncio
import os
import aiohttp
from tqdm.asyncio import tqdm_asyncio
import math
from pathlib import Path
from typing import List, Tuple
import aiofiles
import aioconsole



def select(_title="请选择文件", select_type="file", _multiple=False):
    """打开文件选择窗口，允许用户多选文件并返回列表"""
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    # 提取 explorer.exe 的图标并保存
    explorer_path = r'C:\Windows\explorer.exe'
    ico = icoextract.IconExtractor(explorer_path)
    ico.export_icon('explorer_icon.ico')
    
    root = tk.Tk()
    root.iconbitmap('explorer_icon.ico')
    root.withdraw()  # 隐藏主窗口
    
    if select_type == "file":
        # 设置文件对话框参数
        paths = filedialog.askopenfilenames(
            title=_title,
            filetypes=[ ("cs2比赛记录", "*.html"), ("所有文件", "*.*")],  # 支持自定义过滤器
            multiple=_multiple
        )
    elif select_type == "folder":
        # 文件夹选择模式
        paths = filedialog.askdirectory(
            title=_title,
        ) 
    else:
        raise ValueError("无效的select_type参数，仅支持'file'或'directory'")
    
    root.destroy()  # 销毁窗口
    os.remove('explorer_icon.ico')
    
    if _multiple == False:
        return paths  # 返回单个路径
    elif _multiple == True:
        return list(paths)  # 返回路径列表
    
def extract_demo_links(html_file):
    """
    从HTML文件中提取符合特定结构的回放下载链接
    参数:
        html_file (str): HTML文件路径
    返回:
        list: 包含所有匹配的.dem.bz2链接的列表
    """
    with open(html_file, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    links = []
    # 匹配包含特定class组合的td标签
    for td in soup.find_all('td', class_='csgo_scoreboard_cell_noborder'):
        # 查找包含下载按钮的div标签
        download_div = td.find('div', class_=lambda x: x and {
                               'btnv6_blue_hoverfade', 'btn_medium', 'csgo_scoreboard_btn_gotv'}.issubset(x.split()))
        if download_div:
            # 获取父级a标签的href属性
            a_tag = download_div.find_parent('a')
            if a_tag and a_tag.has_attr('href'):
                links.append(a_tag['href'])
    
    return links

class AsyncDownloader:
    def __init__(
        self,
        urls: List[str],
        download_dir: str = Path(),
        max_workers: int = 4,
        retries: int = 5,
        chunk_size: int = 1024 * 1024,  # 1MB
        max_chunks_per_task: int = 4    # 分片限制
    ):
        self.urls = urls
        self.max_workers = max_workers
        self.retries = retries
        self.chunk_size = chunk_size
        self.max_chunks = max_chunks_per_task
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.progress_bars = {}
        self.downloaded_count = 0  # 下载计数器
        self.files_count = 0  # 文件计数器
        self.filenames = []  # 所有文件名

    async def _get_file_info(self, session: aiohttp.ClientSession, url: str) -> Tuple[int, bool]:
        """获取文件大小和分片支持情况"""
        async with session.head(url) as response:
            if response.status != 200:
                raise ValueError(f"HEAD请求失败: {response.status}")
            
            supports_range = 'bytes' in response.headers.get('Accept-Ranges', '')
            total_size = int(response.headers.get('Content-Length', 0))
            return total_size, supports_range

    def _calculate_chunks(self, total_size: int) -> List[Tuple[int, int]]:
        """计算分片范围"""
        chunk_count = min(math.ceil(total_size / (self.chunk_size * 8)), 8)  # 文件大小除以8自动分片
        chunk_size = total_size // chunk_count
        chunks = []
        for i in range(chunk_count):
            start = i * chunk_size
            end = (i+1)*chunk_size - 1 if i < chunk_count-1 else total_size-1
            chunks.append((start, end))
        return chunks

    async def _download_chunk(
        self,
        session: aiohttp.ClientSession,
        url: str,
        filepath: Path,
        chunk_range: Tuple[int, int]
    ):
        """分片下载核心逻辑"""
        start, end = chunk_range
        headers = {"Range": f"bytes={start}-{end}"}
        progress_key = f"{url}_{start}-{end}"

        for attempt in range(self.retries):
            try:
                async with session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    
                    async with aiofiles.open(filepath, "r+b") as f:
                        await f.seek(start)
                        async for chunk in response.content.iter_chunked(self.chunk_size):
                            await f.write(chunk)
                            self.progress_bars[url].update(len(chunk))
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == self.retries - 1:
                    print(f"\n分片下载失败: {filepath.name} [{start}-{end}] ({str(e)})")
                await asyncio.sleep(2 ** attempt)

    async def _download_file(self, session: aiohttp.ClientSession, url: str):
        """文件下载主逻辑"""
        filename = url.split("/")[-1] or f"file_{hash(url)}"
        filepath = self.download_dir / filename
        
        
        # 获取服务器端文件信息
        total_size, supports_range = await self._get_file_info(session, url)
        if total_size == 0:
            raise ValueError("无法获取文件大小")

        # 检查本地文件是否存在并比较大小
        if filepath.exists():
            local_size = filepath.stat().st_size
            if local_size == total_size:
                print(f"文件 {filename} 已存在且大小相同，跳过下载。")
                self.files_count += 1
                self.filenames.append(filename)
                return  # 直接跳过下载
            else:
                # 用户选择是否覆盖（异步输入）
                choice = await aioconsole.ainput(
                    f"文件 {filename} 已存在但大小不同（本地: {local_size}，服务器: {total_size}） 是否覆盖？(y/n): "
                ).strip().lower()
                if choice != 'y':
                    print(f"跳过下载 {filename}")
                    self.filenames.append(filename)
                    return
                filepath.unlink(missing_ok=True)  # 删除旧文件
                
        try:
            total_size, supports_range = await self._get_file_info(session, url)
            if total_size == 0:
                raise ValueError("无法获取文件大小")

            # 初始化进度条
            self.progress_bars[url] = tqdm_asyncio(
                total=total_size,
                desc=f"下载 {filename}",
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                leave=False
            )

            # 预创建空文件
            if not filepath.exists():
                async with aiofiles.open(filepath, "wb") as f:
                    await f.truncate(total_size)

            # 分片下载逻辑
            if supports_range:
                chunks = self._calculate_chunks(total_size)
                semaphore = asyncio.Semaphore(self.max_chunks)  # 分片专用信号量
                
                tasks = []
                for chunk in chunks:
                    task = asyncio.create_task(self._download_chunk(session, url, filepath, chunk))
                    task.add_done_callback(lambda _: semaphore.release())
                    tasks.append(task)
                
                await asyncio.gather(*tasks)
            else:
                # 回退到整体下载
                async with session.get(url) as response:
                    async with aiofiles.open(filepath, "wb") as f:
                        async for chunk in response.content.iter_chunked(self.chunk_size):
                            await f.write(chunk)
                            self.progress_bars[url].update(len(chunk))
            self.downloaded_count += 1
            self.files_count += 1
            self.filenames.append(filename)
    
        finally:
            if url in self.progress_bars:
                self.progress_bars[url].close()
                del self.progress_bars[url]

    def files_name(self) -> List[str]:
        """返回所有服务器文件名列表"""
        return self.filenames
    
    def downloaded_num(self) -> int:
        """返回已成功下载的文件总数"""
        return self.downloaded_count
    
    def files_num(self) -> int:
        """返回文件总数"""
        return self.files_count

    async def run(self):
        """任务运行入口"""
        semaphore = asyncio.Semaphore(self.max_workers)
        
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False),
            timeout=aiohttp.ClientTimeout(total=3600)
        ) as session:
            tasks = []
            for url in self.urls:
                await semaphore.acquire()
                task = asyncio.create_task(self._download_file(session, url))
                task.add_done_callback(lambda _: semaphore.release())
                tasks.append(task)
            
            await tqdm_asyncio.gather(*tasks, desc=f"总进度",)

    """BZ2文件异步解压器"""

    async def decompress_files(self, file_list, output_dir):
        """
        异步解压BZ2文件列表到指定目录
        
        :param file_list: 要解压的.bz2文件路径列表
        :param output_dir: 解压文件输出目录
        """
        # 创建输出目录（异步执行）
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, os.makedirs, output_dir, exist_ok=True)
        
        # 创建并执行解压任务
        tasks = []
        for input_path in file_list:
            # 构造输出文件名
            input_path = Path(input_path)
            output_filename = input_path.name
            if output_filename.endswith('.bz2'):
                output_filename = output_filename[:-4]  # 去除.bz2后缀
            output_path = Path(output_dir) / output_filename
            
            tasks.append(
                self._decompress_with_error_handling(input_path, output_path)
            )
        
        # 等待所有任务完成（遇到异常继续执行）
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _decompress_with_error_handling(self, input_path, output_path):
        """带错误处理的解压包装方法"""
        try:
            await self.decompress_single_file(input_path, output_path)
        except Exception as e:
            print(f"解压失败 {input_path}: {str(e)}")

    async def decompress_single_file(self, input_path, output_path):
        """单个文件解压任务"""
        await asyncio.to_thread(self._sync_decompress, input_path, output_path)

    @staticmethod
    def _sync_decompress(input_path, output_path):
        """同步解压实现（使用流式处理）"""
        try:
            with bz2.open(input_path, 'rb') as f_in:
                with open(output_path, 'wb') as f_out:
                    while True:
                        chunk = f_in.read(8192)  # 8KB分块读取
                        if not chunk:
                            break
                        f_out.write(chunk)
        except (IOError, bz2.BZ2Error) as e:
            raise RuntimeError(f"解压缩失败: {str(e)}")


if __name__ == "__main__":
    print("请选择 cs2 比赛记录文件")
    selected_files = select("请选择 cs2 比赛记录文件", "file", True)
    print(f"已选则 {len(selected_files)} 个 cs2 比赛记录文件：")
    for file in selected_files:
        print("- ", file)
    
    print("开始解析 html 文件...")   
    if selected_files:
        demo_links = extract_demo_links(selected_files[0])  # 处理第一个文件
        # 或者处理所有文件
        demo_links = []
        file_num =0
        for file in selected_files:
            file_num += 1
            demo_links.extend(extract_demo_links(file))
    print(f"已解析到 {len(demo_links)} 个 Demo 链接 ：")
    for link in demo_links:
        print("- ", link)
    
    print("请选择 Demo 下载文件夹") 
    selected_folder = select("请选择 Demo 下载文件夹", "folder")     
    print(f"已选择的 Demo 文件夹 {selected_folder} ")
    
    print("开始下载 Demo 文件...")
    tmp_folder = selected_folder + "/tmp"
    downloader = AsyncDownloader(
        urls=demo_links,
        download_dir=tmp_folder,
        max_workers=4,  # 最大并发下载任务数
        max_chunks_per_task=4  # 单任务最大分片数
    )
    asyncio.run(downloader.run())
    print(f"\n已下载 {downloader.downloaded_num()} 个 Demo 文件, 当前存在 {downloader.files_num()} 个 Demo 文件:")
    for file in downloader.files_name():
        print("- ", file)

    print("开始解压 Demo 文件至目标文件夹...")
