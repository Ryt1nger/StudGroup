"""Owner UI is a real, readable dashboard without oval donuts or mock numbers."""

pytest_plugins = ["test_schedule_api"]


def test_operator_dashboard_uses_native_charts_and_dark_design(client):
    html = client.get("/admin/")
    style = client.get("/admin/style.css")
    visuals = client.get("/admin/visuals.js")
    app = client.get("/admin/app.js")

    for response in (html, style, visuals, app):
        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]

    assert 'class="nav-icon"' in html.text
    assert 'id="chart-workload"' in html.text
    assert 'id="chart-outcomes"' in html.text
    assert 'id="chart-finance-kind"' in html.text
    assert 'id="chart-errors"' in html.text

    assert "color-scheme:dark" in style.text
    assert "grid-template-columns:repeat(6,minmax(0,1fr))" in style.text
    assert "aspect-ratio:1" in style.text
    assert 'viewBox:"0 0 240 240"' in visuals.text
    assert 'preserveAspectRatio:"xMidYMid meet"' in visuals.text
    assert 'preserveAspectRatio:"none"' not in visuals.text

    # Correct Russian date formatting rather than raw ISO date strings.
    assert 'toLocaleDateString("ru-RU"' in visuals.text
    # Fractional dollar amounts must not be rounded to whole dollars.
    assert "currency?dollars(total):nf.format(total)" in visuals.text

    # Real data endpoints and interactive drilldowns remain intact.
    assert "/v1/admin/visuals?" in app.text
    assert "/v1/admin/analytics?" in app.text
    assert "/v1/admin/details/" in app.text
    assert "data-detail" in app.text
