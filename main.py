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

from datetime import datetime

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
        group_name = "ΜυМа.εхε"

        try:
            if event.message_obj.group:
                group_name = event.message_obj.group.group_name
        except Exception:
            pass
        
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
            
            cols = get_last_4_days_columns(df)
            cols = sort_date_columns(cols)

            result = df[["榜区"] + cols].copy()

            # 增加最后一天与前一天的变化
            result = add_change_column(result, cols)

            today_col = f"{datetime.now().month}月{datetime.now().day}日"

            if today_col in result.columns:

                def sort_key(value):
                    value = str(value).strip()

                    # 带「神」的值排在所有普通数字后面
                    m = re.search(r"(\d+(?:\.\d+)?)\s*神", value)
                    if m:
                        return (1, -float(m.group(1)))

                    # 普通数字，按数值从大到小
                    try:
                        return (0, -float(value))
                    except ValueError:
                        return (2, 0)

                result = (
                    result.assign(
                        _sort_key=result[today_col].map(sort_key)
                    )
                    .sort_values(
                        by="_sort_key",
                        ascending=True,
                        kind="stable",
                    )
                    .drop(columns="_sort_key")
                    .reset_index(drop=True)
                )
            
            logger.info("\n{}", result.to_string())
            logger.info("开始渲染成图片")
            path = df_to_image(
                result,
                self.font_path,
                active_sheet['name'],
                group_name,
                "每日榜线.png"
            )
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
    支持格式：
    2026年4.9-7.9（xxx）
    2026年4.9-2027年7.9（xxx）

    返回：
    (start, end)
    """
    m = re.search(
        r"(\d{4})年(\d+)\.(\d+)-(?:(\d{4})年)?(\d+)\.(\d+)",
        name,
    )
    if not m:
        return None

    start_year = int(m.group(1))
    start_month = int(m.group(2))
    start_day = int(m.group(3))

    end_year = int(m.group(4)) if m.group(4) else start_year
    end_month = int(m.group(5))
    end_day = int(m.group(6))

    start = datetime(start_year, start_month, start_day)
    end = datetime(end_year, end_month, end_day)

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


def get_last_4_days_columns(df):
    now = datetime.now()

    # 05:00 前偏移 4 天，05:00 后偏移 3 天
    start = 4 if now.hour < 5 else 3

    cols = []

    for i in range(start, start - 4, -1):
        d = now - timedelta(days=i)
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

def parse_star_count(value):
    """提取星星数量，兼容普通数字和带神的数值"""
    if pd.isna(value):
        return None

    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))

    return float(match.group()) if match else None


def add_change_column(df, date_cols):
    df = df.copy()

    if len(date_cols) < 2:
        df["日增减"] = "—"
        return df

    previous_col = date_cols[-2]
    latest_col = date_cols[-1]

    def calculate(row):
        previous = parse_star_count(row[previous_col])
        latest = parse_star_count(row[latest_col])

        if previous is None or latest is None:
            return "—"

        diff = latest - previous

        if diff > 0:
            return f"↑{diff:g}"
        elif diff < 0:
            return f"↓{abs(diff):g}"
        else:
            return "—"

    df["日增减"] = df.apply(calculate, axis=1)

    return df

def df_to_image(df, font_path, title, group_name, output="榜单.png"):
    font_prop = fm.FontProperties(fname=font_path)
    plt.rcParams['axes.unicode_minus'] = False

    # 固定画布尺寸
    fig = plt.figure(figsize=(7, 7), dpi=300)

    # 上方：表格区域
    ax = fig.add_axes([0.05, 0.20, 0.90, 0.75])
    ax.axis('off')

    table = ax.table(
        cellText=df.values,
        colLabels=df.columns,
        cellLoc='center',
        loc='center',
        bbox=[0, 0, 1, 1]
    )

    table.auto_set_font_size(False)
    table.set_fontsize(14)
    table.scale(1.0, 1.0)

    # 应用字体
    for cell in table.get_celld().values():
        cell.set_text_props(fontproperties=font_prop)

    # 表头
    for j in range(len(df.columns)):
        table[(0, j)].set_text_props(
            fontproperties=font_prop,
            size=16,
            weight='bold'
        )

    change_col_index = df.columns.get_loc("日增减") if "日增减" in df.columns else -1

    # 高亮前三
    for i in range(len(df)):
        for j in range(len(df.columns)):
            cell = table[(i + 1, j)]

            cell.set_text_props(
                fontproperties=font_prop,
                size=16
            )

            if i == 0:
                cell.set_facecolor("#FFD700")
            elif i == 1:
                cell.set_facecolor("#C0C0C0")
            elif i == 2:
                cell.set_facecolor("#CD7F32")

            # 变化列字体颜色
            if j == change_col_index:
                value = str(df.iloc[i, j])

                if value.startswith("↑"):
                    cell.get_text().set_color("#E53935")
                elif value.startswith("↓"):
                    cell.get_text().set_color("#43A047")
                else:
                    cell.get_text().set_color("gray")

    # 下方：文字说明区域
    update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

    footer_lines = [
        (f"当前赛季：{title}", "#1677FF", 15),
        ("数据来源：https://kdocs.cn/l/ch0MppmOWCDV", "gray", 11),
        (f"图片来源：{group_name}", "gray", 11),
        (update_time, "gray", 11)
    ]

    footer_start_y = 0.16
    footer_spacing = 0.04

    for i, (text, color, size) in enumerate(footer_lines):
        fig.text(
            0.5,
            footer_start_y - i * footer_spacing,
            text,
            fontproperties=font_prop,
            fontsize=size,
            ha='center',
            va='center',
            color=color
        )

    # 保存图片，不使用 bbox_inches='tight'
    fig.savefig(output, dpi=300)
    plt.close(fig)

    return output
