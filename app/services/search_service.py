from urllib.parse import urlparse

from ddgs import DDGS

from app.services.errors import ServiceError


class SearchService:
    def research(self, context):
        # Only company/position/type are sent to search engines; never resumes or API keys.
        company, position = context["company"], context["position"]
        queries = [f'{company} {position} 招聘 岗位职责 面试', f'{company} {context["interview_type"]} 面经']
        results = {}
        failed = 0
        for query in queries:
            try:
                rows = DDGS(timeout=12).text(query, region="cn-zh", max_results=5, backend="bing,brave,duckduckgo")
            except Exception:
                failed += 1
                continue
            for row in rows:
                url = row.get("href", "")
                if urlparse(url).scheme in {"http", "https"} and row.get("title"):
                    results[url] = {"url": url, "title": row["title"], "snippet": row.get("body", "")[:1500]}
        if not results:
            raise ServiceError("联网搜索未获取到资料，请检查网络并重试；不会把模型记忆当作联网研究结果。")
        return {"results": list(results.values()), "limitations": [
            "资料来自搜索结果摘要，未读取网页全文；面试流程和时效性仍需核实。",
            *(["部分搜索失败，资料覆盖可能不完整。"] if failed else []),
        ]}
