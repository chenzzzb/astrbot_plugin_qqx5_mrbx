from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from loguru import logger

from .api import WPS365DBSheetAPI, WPSAPIError
from .config import load_settings

import os
import pandas as pd
from datetime import datetime, timedelta
import re
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

@register("QQ炫舞手游排位每日榜线", "Chenzb", "可以获取每日榜线图片的小插件", "1.0.0")
class QQX5MrbxPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.font_path = os.path.join(self.base_dir, "fonts", "default.ttf")

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    # 注册指令的装饰器。指令名为 每日榜线。注册成功后，发送 `/每日榜线` 就会触发这个指令
    @filter.command("每日榜线")
    async def mrbx(self, event: AstrMessageEvent):
        settings = load_settings(self.base_dir)
        client_id = settings["client_id"]
        client_secret = settings["client_secret"]
        file_id = settings["file_id"]

        api = WPS365DBSheetAPI(
            client_id=client_id,
            client_secret=client_secret,
        )

        if not client_id or not client_secret:
            logger.error("请先设置环境变量 WPS_CLIENT_ID、WPS_CLIENT_SECRET")
            yield event.plain_result(f"请先设置环境变量 WPS_CLIENT_ID、WPS_CLIENT_SECRET")
        if not file_id:
            logger.error("请先设置环境变量 WPS_FILE_ID")
            yield event.plain_result(f"请先设置环境变量 WPS_FILE_ID")

        try:
            logger.info("获取 Sheets...")
            result = api.get_sheets(file_id)
            active_sheet = find_active_sheet(result)

            if active_sheet:
                logger.info(f"当前命中的 sheet: {active_sheet['name']}")
                sheet_id = active_sheet["sheet_id"]
            else:
                yield event.plain_result(f"没有匹配到当前日期的 sheet")

            data = api.get_range_data(
                file_id,
                worksheet_id=sheet_id,
                row_from=0,
                row_to=12,
                col_from=0,
                col_to=100,
            )
            logger.info(data)
            df = wps_to_df(data["data"]["range_data"])
            logger.info("\n{}", df.to_string())
            # rank_df = get_today_rank(df)

            cols = get_last_3_days_columns(df)
            cols = sort_date_columns(cols)
            result = df[["榜区"] + cols]
            logger.info("\n{}", result.to_string())
            logger.info("开始渲染成图片")
            path = df_to_image(result, self.font_path, active_sheet['name'], "每日榜线.png")
            logger.info("渲染成图片完成 ", path)
            yield event.image_result(path)
        except WPSAPIError as exc:
            logger.error({
                "message": str(exc),
                "status_code": exc.status_code,
                "payload": exc.payload,
            })

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""

def parse_sheet_range(name: str):
    """
    解析：
    2026年4.9-7.9（ssp）
    → (2026-04-09, 2026-07-09)
    """

    m = re.search(r"(\d{4})年(\d+)\.(\d+)-(\d+)\.(\d+)", name)
    if not m:
        return None

    year = int(m.group(1))
    m1, d1 = int(m.group(2)), int(m.group(3))
    m2, d2 = int(m.group(4)), int(m.group(5))

    start = datetime(year, m1, d1)
    end = datetime(year, m2, d2)

    return start, end

def find_active_sheet(result):
    now = datetime.now()

    for sheet in result:

        if sheet.get("hidden"):
            continue

        name = sheet.get("name", "")
        rng = parse_sheet_range(name)

        if not rng:
            continue

        start, end = rng

        if start <= now <= end:
            return sheet

    return None

def wps_to_df(range_data):
    # 找最大行列
    max_row = max(cell["row_from"] for cell in range_data)
    max_col = max(cell["col_from"] for cell in range_data)

    # 初始化矩阵
    matrix = [[""] * (max_col + 1) for _ in range(max_row + 1)]

    # 填数据
    for cell in range_data:
        r = cell["row_from"]
        c = cell["col_from"]

        v = cell.get("cell_text") or cell.get("original_cell_value") or ""
        matrix[r][c] = v

    # 转 DataFrame
    df = pd.DataFrame(matrix)

    # 第一行做表头
    df.columns = df.iloc[0]
    df = df[1:].reset_index(drop=True)

    return df

def get_last_3_days_columns(df):
    cols = []

    for i in range(3):
        d = datetime.now() - timedelta(days=i)
        col = f"{d.month}月{d.day}日"

        if col in df.columns:
            cols.append(col)

    return cols

def sort_date_columns(cols):
    def parse(col):
        # 提取 月 和 日
        m = re.match(r"(\d+)月(\d+)日", col)
        if not m:
            return datetime.max  # 非日期列放最后
        month, day = int(m.group(1)), int(m.group(2))
        return datetime(2026, month, day)  # 年份随便给当前年即可

    return sorted(cols, key=parse)

def df_to_image(df, font_path, title, output="榜单.png"):
    font_prop = fm.FontProperties(fname=font_path)
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.axis('off')

    ax.set_title(title, fontproperties=font_prop, fontsize=18, weight='bold')
    plt.subplots_adjust(top=0.75)

    table = ax.table(
        cellText=df.values,
        colLabels=df.columns,
        cellLoc='center',
        loc='center',
        bbox=[0, 0, 1, 0.95]
    )

    table.auto_set_font_size(False)
    table.set_fontsize(14)
    table.scale(1.0, 2.0)

    # 应用字体（解决乱码）
    for cell in table.get_celld().values():
        cell.set_text_props(fontproperties=font_prop)

    for j in range(len(df.columns)):
        table[(0, j)].set_text_props(fontproperties=font_prop, size=14, weight='bold')

    # 高亮前三
    for i in range(len(df)):
        for j in range(len(df.columns)):
            cell = table[(i + 1, j)]
            cell.set_text_props(fontproperties=font_prop, size=14)
            if i == 0:
                cell.set_facecolor("#FFD700")
            elif i == 1:
                cell.set_facecolor("#C0C0C0")
            elif i == 2:
                cell.set_facecolor("#CD7F32")

    plt.savefig(output, bbox_inches='tight', dpi=300)
    plt.close()

    return output