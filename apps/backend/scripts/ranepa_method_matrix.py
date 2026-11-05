"""Independent read-only diagnostics of timetable retrieval channels.

Performs unauthenticated GET only. Outputs status, content-type and anonymised
challenge/auth signals; never raw page bodies, cookies, tokens or response headers.
"""

import asyncio
import json
from urllib.parse import urlparse

import httpx

URLS = {
    "public_group_page": "https://my.ranepa.ru/schedule/?by=group&f=26d97dfd-7f9b-11f1-9fac-00620bc81861&type=schedule",
    "public_group_search": "https://my.ranepa.ru/schedule/",
    "student_cabinet_schedule": "https://my.ranepa.ru/lk/student/schedule",
    "student_cabinet_login": "https://my.ranepa.ru/lk/login",
    "student_cabinet_api_root": "https://my.ranepa.ru/lk/n-api/",
    "moodle_my": "https://lms.ranepa.ru/my/",
    "moodle_upcoming_calendar": "https://lms.ranepa.ru/calendar/view.php?view=upcoming",
    "moodle_calendar_export": "https://lms.ranepa.ru/calendar/export.php",
    "moodle_rest_site_info": "https://lms.ranepa.ru/webservice/rest/server.php?wsfunction=core_webservice_get_site_info&moodlewsrestformat=json",
    "external_client_repo_api": "https://api.github.com/repos/Ekanam-Friends/Ikanam-schedule",
    "external_client_api_notes": "https://raw.githubusercontent.com/Ekanam-Friends/Ikanam-schedule/main/docs/api-notes.md",
}

def flags(body: str) -> dict:
    text = body.lower()
    return {
        "captcha": any(s in text for s in ("captcha", "капча", "recaptcha", "cf-chl", "challenge-platform")),
        "login": any(s in text for s in ("login", "войти", "авторизация", "password", "пароль")),
        "calendar": any(s in text for s in ("расписание", "schedule", "calendar", "календарь")),
        "moodle_token_error": any(s in text for s in ("invalidtoken", "invalid token", "accessexception")),
        "likely_lesson": any(s in text for s in ("лекционные занятия", "практические занятия", "семинарские занятия")),
    }

async def probe(client, name, url):
    try:
        response = await client.get(url)
        host = urlparse(str(response.url)).hostname
        return {
            "method": name,
            "status": response.status_code,
            "final_host": host,
            "final_path": urlparse(str(response.url)).path,
            "redirects": [
                {"status": step.status_code, "host": urlparse(str(step.url)).hostname}
                for step in response.history
            ],
            "content_type": response.headers.get("content-type", "").split(";")[0],
            "bytes": len(response.content),
            "signals": flags(response.text[:150000]),
        }
    except httpx.HTTPError as error:
        return {"method": name, "error_type": type(error).__name__}


async def main():
    async with httpx.AsyncClient(
        timeout=18,
        follow_redirects=True,
        max_redirects=4,
        trust_env=False,
        headers={"Accept": "text/html,application/json;q=0.9,*/*;q=0.8"},
    ) as client:
        results = []
        for name, url in URLS.items():
            results.append(await probe(client, name, url))
            print(json.dumps(results[-1], ensure_ascii=False))
    return results


if __name__ == "__main__":
    asyncio.run(main())
