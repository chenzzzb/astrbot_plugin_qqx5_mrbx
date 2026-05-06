import json
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests
from loguru import logger


class WPSAPIError(Exception):
    def __init__(self, message: str, *, status_code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class WPS365DBSheetAPI:
    """WPS 365 多维表格 API 封装

    设计要点：
    - v7 接口统一风格为 `POST /{entity}/{action}`，仅 /schema 为 GET
    - 记录层 `fields_value` 官方约定为 **JSON 字符串**，本封装在入口处统一序列化
    - 查询辅助（sheets / fields / views）直接从 schema 派生，避免猜测接口路径
    """

    BASE_URL = "https://openapi.wps.cn"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        timeout: int = 30,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.app_id = app_id or client_id
        self.app_secret = app_secret or client_secret
        self.access_token: Optional[str] = None
        self.base_url = self.BASE_URL
        self.timeout = timeout

    # ---------------------------------------------------------------------
    # 鉴权与签名
    # ---------------------------------------------------------------------
    def get_access_token(self) -> str:
        if self.access_token:
            return self.access_token

        try:
            response = requests.post(
                f"{self.base_url}/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise WPSAPIError("获取 access_token 请求失败") from exc

        logger.info(f"应用授权获取 token 响应状态: {response.status_code}")
        try:
            result = response.json()
        except ValueError as exc:
            raise WPSAPIError("获取 access_token 响应不是合法 JSON", status_code=response.status_code) from exc
        self.access_token = result.get("access_token")
        if not self.access_token:
            raise WPSAPIError("应用授权获取 access_token 失败", status_code=response.status_code, payload=result)
        return self.access_token

    def _build_url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _generate_kso1_signature(self, method: str, path: str, date_str: str, body: str = "") -> str:
        sha256_hex = hashlib.sha256(body.encode("utf-8")).hexdigest() if body else ""
        sign_string = f"KSO-1{method}{path}application/json{date_str}{sha256_hex}"
        logger.debug(f"签名请求: {method} {path}")
        signature_hex = hmac.new(
            self.app_secret.encode("utf-8"),
            sign_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"KSO-1 {self.app_id}:{signature_hex}"

    def _get_headers(self, method: str = "GET", path: str = "", body: str = "") -> Dict[str, str]:
        if not self.access_token:
            self.get_access_token()

        date_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "X-Kso-Date": date_str,
            "X-Kso-Authorization": self._generate_kso1_signature(method, path, date_str, body),
        }

    # ---------------------------------------------------------------------
    # 基础请求
    # ---------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        raw_body: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
        files: Optional[Dict[str, Any]] = None,
        use_kso1: bool = True,
    ) -> Dict[str, Any]:
        request_method = method.upper()
        query_string = urlencode(params or {})
        signed_path = f"{path}?{query_string}" if query_string else path
        body = raw_body or (json.dumps(json_data, ensure_ascii=False) if json_data is not None else "")

        headers = self._get_headers(request_method, signed_path, body) if use_kso1 else {
            "Authorization": f"Bearer {self.get_access_token()}"
        }
        if extra_headers:
            headers.update(extra_headers)
        if files:
            headers.pop("Content-Type", None)

        try:
            response = requests.request(
                request_method,
                self._build_url(path),
                headers=headers,
                params=params,
                json=json_data,
                data=raw_body if raw_body else data,
                files=files,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise WPSAPIError(f"请求失败: {request_method} {path}") from exc

        logger.info(f"{request_method} {signed_path} -> {response.status_code}")

        try:
            result = response.json()
        except ValueError:
            result = response.text

        if response.status_code < 200 or response.status_code >= 300:
            raise WPSAPIError(
                f"接口请求失败: {request_method} {path}",
                status_code=response.status_code,
                payload=result,
            )
        return result if isinstance(result, dict) else {"data": result}

    # ---------------------------------------------------------------------
    # 内部工具
    # ---------------------------------------------------------------------
    @staticmethod
    def _stringify_fields(fields: Optional[Dict[str, Any]]) -> str:
        """WPS 记录接口的 fields_value 约定为 JSON 字符串"""
        return json.dumps(fields or {}, ensure_ascii=False)

    def _schema_sheets(self, file_id: str) -> List[Dict[str, Any]]:
        schema = self.get_schema(file_id)
        return schema.get("data", {}).get("sheets", []) or []

    def _find_sheet(self, file_id: str, sheet_id: Optional[str], sheet_name: Optional[str]) -> Dict[str, Any]:
        sheets = self._schema_sheets(file_id)
        if sheet_id is not None:
            target = str(sheet_id)
            for sheet in sheets:
                if str(sheet.get("id")) == target:
                    return sheet
        if sheet_name:
            for sheet in sheets:
                if sheet.get("name") == sheet_name:
                    return sheet
        raise WPSAPIError(f"未找到 sheet_id={sheet_id} / sheet_name={sheet_name}")

    def _resolve_sheet_id(self, file_id: str, sheet_id: Optional[str], sheet_name: Optional[str]) -> str:
        if sheet_id is not None:
            return str(sheet_id)
        if not sheet_name:
            raise WPSAPIError("必须提供 sheet_id 或 sheet_name")
        return str(self._find_sheet(file_id, None, sheet_name).get("id"))

    # ---------------------------------------------------------------------
    # Schema
    # ---------------------------------------------------------------------
    def get_schema(self, file_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v7/sheets/{file_id}/worksheets")

    # ---------------------------------------------------------------------
    # 工作表 (Sheet)
    # ---------------------------------------------------------------------
    def get_sheets(self, file_id: str) -> List[Dict[str, Any]]:
        return self._schema_sheets(file_id)

    def get_sheet_info(self, file_id: str, sheet_id: Optional[str] = None, sheet_name: Optional[str] = None) -> Dict[str, Any]:
        return self._find_sheet(file_id, sheet_id, sheet_name)

    def get_sheet_id_by_name(self, file_id: str, sheet_name: str) -> Optional[str]:
        for sheet in self._schema_sheets(file_id):
            if sheet.get("name") == sheet_name:
                return str(sheet.get("id"))
        logger.warning(f"未找到名为 '{sheet_name}' 的工作表")
        return None

    # ---------------------------------------------------------------------
    # 视图 (View)
    # ---------------------------------------------------------------------
    def get_views(self, file_id: str, sheet_id: Optional[str] = None, sheet_name: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._find_sheet(file_id, sheet_id, sheet_name).get("views", []) or []

    def get_range_data(
        self,
        file_id: str,
        worksheet_id: int,
        row_from: int,
        row_to: int,
        col_from: int,
        col_to: int,
    ) -> Dict[str, Any]:
        """
        获取单元格区域数据

        :param file_id: 文件ID
        :param worksheet_id: 工作表ID（不是 index，是接口返回的 id）
        :param row_from: 起始行（从0开始）
        :param row_to: 结束行
        :param col_from: 起始列（从0开始）
        :param col_to: 结束列
        """
        return self._request(
            "GET",
            f"/v7/sheets/{file_id}/worksheets/{worksheet_id}/range_data",
            params={
                "row_from": row_from,
                "row_to": row_to,
                "col_from": col_from,
                "col_to": col_to,
            },
        )

