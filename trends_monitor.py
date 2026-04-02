import os
import pandas as pd
from datetime import datetime, timedelta
import schedule
import time
import random
import shutil
from querytrends import batch_get_queries, save_related_queries, RequestLimiter
import json
import logging
import backoff
import argparse
from html import escape
from config import (
    EMAIL_CONFIG, 
    KEYWORDS, 
    KEYWORD_GROUPS,
    RATE_LIMIT_CONFIG, 
    SCHEDULE_CONFIG,
    MONITOR_CONFIG,
    LOGGING_CONFIG,
    STORAGE_CONFIG,
    TRENDS_CONFIG,
    NOTIFICATION_CONFIG
)
from notification import NotificationManager

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOGGING_CONFIG['level']),
    format=LOGGING_CONFIG['format'],
    handlers=[
        logging.FileHandler(LOGGING_CONFIG['log_file']),
        logging.StreamHandler()
    ]
)

# 创建请求限制器实例
request_limiter = RequestLimiter()

# 创建通知管理器实例
notification_manager = NotificationManager()

def create_daily_directory():
    """Create a directory for today's data"""
    today = datetime.now().strftime('%Y%m%d')
    directory = f"{STORAGE_CONFIG['data_dir_prefix']}{today}"
    if not os.path.exists(directory):
        os.makedirs(directory)
    return directory

def check_rising_trends(data, keyword, threshold=MONITOR_CONFIG['rising_threshold']):
    """Check if any rising trends exceed the threshold"""
    if not data or 'rising' not in data or data['rising'] is None:
        return []
    
    rising_trends = []
    df = data['rising']
    if isinstance(df, pd.DataFrame):
        for _, row in df.iterrows():
            if row['value'] > threshold:
                rising_trends.append((row['query'], row['value']))
    return rising_trends

def generate_daily_report(results, directory):
    """Generate a daily report in CSV format"""
    report_data = []
    
    for keyword, data in results.items():
        if data and isinstance(data.get('rising'), pd.DataFrame):
            rising_df = data['rising']
            for _, row in rising_df.iterrows():
                report_data.append({
                    'keyword': keyword,
                    'related_keywords': row['query'],
                    'value': row['value'],
                    'type': 'rising'
                })
        
        if data and isinstance(data.get('top'), pd.DataFrame):
            top_df = data['top']
            for _, row in top_df.iterrows():
                report_data.append({
                    'keyword': keyword,
                    'related_keywords': row['query'],
                    'value': row['value'],
                    'type': 'top'
                })
    
    if report_data:
        df = pd.DataFrame(report_data)
        filename = f"{STORAGE_CONFIG['report_filename_prefix']}{datetime.now().strftime('%Y%m%d')}.csv"
        report_file = os.path.join(directory, filename)
        df.to_csv(report_file, index=False)
        return report_file
    return None

def _load_keyword_snapshots(directory):
    """Load per-keyword JSON files from a completed run."""
    snapshots_by_keyword = {}
    prefix = STORAGE_CONFIG['json_filename_prefix']

    for filename in sorted(os.listdir(directory)):
        if not (filename.startswith(prefix) and filename.endswith('.json')):
            continue

        filepath = os.path.join(directory, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            payload = json.load(f)

        keyword = payload.get('keyword', '')
        snapshots_by_keyword[keyword] = {
            'keyword': payload.get('keyword', ''),
            'timestamp': payload.get('timestamp', ''),
            'filename': filename,
            'top': payload.get('related_queries', {}).get('top') or [],
            'rising': payload.get('related_queries', {}).get('rising') or [],
        }

    ordered_snapshots = []
    for keyword in KEYWORDS:
        if keyword in snapshots_by_keyword:
            ordered_snapshots.append(snapshots_by_keyword[keyword])

    for keyword in sorted(k for k in snapshots_by_keyword if k not in KEYWORDS):
        ordered_snapshots.append(snapshots_by_keyword[keyword])

    return ordered_snapshots

def _copy_run_assets(directory, target_dir):
    """Copy generated CSV and JSON files into the static site directory."""
    os.makedirs(target_dir, exist_ok=True)

    copied_files = []
    for filename in sorted(os.listdir(directory)):
        if not (filename.endswith('.json') or filename.endswith('.csv')):
            continue

        source = os.path.join(directory, filename)
        destination = os.path.join(target_dir, filename)
        shutil.copy2(source, destination)
        copied_files.append(filename)

    return copied_files

def _render_rows_table(rows, value_label):
    if not rows:
        return "<p class='empty'>No data available.</p>"

    rendered_rows = []
    for row in rows[:10]:
        rendered_rows.append(
            "<tr>"
            f"<td>{escape(str(row.get('query', '')))}</td>"
            f"<td>{escape(str(row.get('value', '')))}</td>"
            "</tr>"
        )

    return (
        "<table>"
        "<thead><tr><th>Query</th><th>" + escape(value_label) + "</th></tr></thead>"
        "<tbody>" + ''.join(rendered_rows) + "</tbody>"
        "</table>"
    )

def _slugify(value):
    chars = []
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
        elif char in [' ', '-', '_']:
            chars.append('-')

    slug = ''.join(chars).strip('-')
    while '--' in slug:
        slug = slug.replace('--', '-')
    return slug or 'group'

def _build_group_payload(snapshots):
    snapshots_by_keyword = {snapshot['keyword']: snapshot for snapshot in snapshots}
    grouped = []
    seen_keywords = set()

    for group in KEYWORD_GROUPS:
        group_snapshots = []
        missing_keywords = []

        for keyword in group['keywords']:
            snapshot = snapshots_by_keyword.get(keyword)
            if snapshot:
                group_snapshots.append(snapshot)
                seen_keywords.add(keyword)
            else:
                missing_keywords.append(keyword)

        grouped.append({
            'name': group['name'],
            'slug': _slugify(group['name']),
            'snapshots': group_snapshots,
            'missing_keywords': missing_keywords,
            'configured_keywords': len(group['keywords']),
            'successful_keywords': len(group_snapshots),
        })

    uncategorized = [snapshot for snapshot in snapshots if snapshot['keyword'] not in seen_keywords]
    if uncategorized:
        grouped.append({
            'name': 'Uncategorized',
            'slug': 'uncategorized',
            'snapshots': uncategorized,
            'missing_keywords': [],
            'configured_keywords': len(uncategorized),
            'successful_keywords': len(uncategorized),
        })

    return grouped

def _render_group_nav(group_payload):
    pills = []
    for group in group_payload:
        pills.append(
            "<a class='group-pill' href='#" + escape(group['slug']) + "'>"
            f"<span>{escape(group['name'])}</span>"
            f"<strong>{group['successful_keywords']}/{group['configured_keywords']}</strong>"
            "</a>"
        )

    return ''.join(pills)

def _render_keyword_sections(snapshots):
    sections = []

    for snapshot in snapshots:
        sections.append(
            "<section class='keyword-card'>"
            f"<h3>{escape(snapshot['keyword'])}</h3>"
            f"<p class='meta'>Snapshot time: {escape(snapshot['timestamp'])}</p>"
            "<div class='grid two-col'>"
            "<div>"
            "<h4>Rising Queries</h4>"
            f"{_render_rows_table(snapshot.get('rising', []), 'Growth')}"
            "</div>"
            "<div>"
            "<h4>Top Queries</h4>"
            f"{_render_rows_table(snapshot.get('top', []), 'Score')}"
            "</div>"
            "</div>"
            "</section>"
        )

    return ''.join(sections) if sections else "<p class='empty'>No keyword snapshots were generated.</p>"

def _render_group_sections(group_payload):
    sections = []

    for group in group_payload:
        missing_note = ""
        if group['missing_keywords']:
            missing_note = (
                "<p class='meta'>Missing in this run: "
                + escape(', '.join(group['missing_keywords']))
                + "</p>"
            )

        sections.append(
            "<section class='group-block' id='" + escape(group['slug']) + "'>"
            "<div class='group-header'>"
            f"<div><h2>{escape(group['name'])}</h2>"
            f"<p class='meta'>{group['successful_keywords']} of {group['configured_keywords']} keywords produced data.</p>"
            f"{missing_note}</div>"
            "</div>"
            "<div class='grid'>"
            f"{_render_keyword_sections(group['snapshots'])}"
            "</div>"
            "</section>"
        )

    return ''.join(sections) if sections else "<p class='empty'>No grouped keyword data is available.</p>"

def _render_alerts_table(high_rising_trends):
    if not high_rising_trends:
        return "<p class='empty'>No high-growth alerts for this run.</p>"

    rows = []
    for item in high_rising_trends:
        rows.append(
            "<tr>"
            f"<td>{escape(item['keyword'])}</td>"
            f"<td>{escape(item['query'])}</td>"
            f"<td>{escape(str(item['value']))}%</td>"
            "</tr>"
        )

    return (
        "<table>"
        "<thead><tr><th>Base Keyword</th><th>Related Query</th><th>Growth</th></tr></thead>"
        "<tbody>" + ''.join(rows) + "</tbody>"
        "</table>"
    )

def _render_history_list(history_entries):
    items = []
    for entry in history_entries[:14]:
        items.append(
            "<li>"
            f"<a href='{escape(entry['relative_dir'])}/'>{escape(entry['date'])}</a>"
            f" <span>{escape(entry['summary'])}</span>"
            "</li>"
        )

    return ''.join(items) if items else "<li>No history yet.</li>"

def _build_site_html(site_payload, history_entries):
    summary = site_payload['summary']
    files = site_payload['files']
    group_payload = site_payload['grouped_snapshots']

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TrendSpy Daily Report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f1e8;
      --surface: #fffdf7;
      --surface-strong: #f0e6d2;
      --text: #14213d;
      --muted: #5c677d;
      --border: #d8cdb8;
      --accent: #c26a2d;
      --accent-soft: #f3d9c2;
      --success: #227c5d;
      --shadow: 0 16px 40px rgba(20, 33, 61, 0.08);
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(194, 106, 45, 0.14), transparent 32%),
        linear-gradient(180deg, #f8f5ee 0%, var(--bg) 100%);
      color: var(--text);
    }}
    main {{
      max-width: 1160px;
      margin: 0 auto;
      padding: 48px 20px 80px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(255, 253, 247, 0.95), rgba(240, 230, 210, 0.92));
      border: 1px solid var(--border);
      border-radius: 28px;
      padding: 32px;
      box-shadow: var(--shadow);
    }}
    h1, h2, h3, h4 {{
      margin: 0 0 12px;
      font-family: Georgia, "Times New Roman", serif;
      font-weight: 600;
    }}
    p {{
      margin: 0;
      line-height: 1.6;
    }}
    .hero p {{
      color: var(--muted);
      max-width: 760px;
    }}
    .grid {{
      display: grid;
      gap: 16px;
    }}
    .cards {{
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      margin-top: 24px;
    }}
    .two-col {{
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    }}
    .card, .keyword-card, .panel {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 22px;
      padding: 22px;
      box-shadow: var(--shadow);
    }}
    .card-value {{
      display: block;
      margin-top: 8px;
      font-size: 2rem;
      font-weight: 700;
      color: var(--accent);
    }}
    .section {{
      margin-top: 28px;
    }}
    .meta {{
      color: var(--muted);
      font-size: 0.95rem;
      margin-bottom: 12px;
    }}
    .links {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 18px;
    }}
    .links a {{
      display: inline-flex;
      align-items: center;
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
      text-decoration: none;
      font-weight: 600;
    }}
    .group-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 18px;
    }}
    .group-pill {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: linear-gradient(180deg, var(--surface) 0%, var(--surface-strong) 100%);
      color: var(--text);
      text-decoration: none;
      font-weight: 600;
    }}
    .group-pill strong {{
      color: var(--accent);
      font-size: 0.95rem;
    }}
    .group-block {{
      margin-top: 28px;
    }}
    .group-header {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 12px;
      margin-bottom: 14px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
    }}
    th, td {{
      text-align: left;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(216, 205, 184, 0.8);
      vertical-align: top;
    }}
    th {{
      font-size: 0.85rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
    }}
    .empty {{
      color: var(--muted);
      padding-top: 8px;
    }}
    .history-list {{
      margin: 0;
      padding-left: 18px;
    }}
    .history-list li {{
      margin: 10px 0;
      color: var(--muted);
    }}
    .history-list a {{
      color: var(--accent);
      font-weight: 600;
      text-decoration: none;
      margin-right: 8px;
    }}
    .footer {{
      margin-top: 32px;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    @media (max-width: 720px) {{
      main {{
        padding: 24px 14px 56px;
      }}
      .hero, .card, .keyword-card, .panel {{
        border-radius: 18px;
        padding: 18px;
      }}
      .card-value {{
        font-size: 1.7rem;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>TrendSpy Daily Report</h1>
      <p>Automatically refreshed Google Trends related-query snapshots, published by GitHub Actions and served by GitHub Pages.</p>
      <div class="grid cards">
        <article class="card">
          <h2>Run Date</h2>
          <span class="card-value">{escape(site_payload['date'])}</span>
        </article>
        <article class="card">
          <h2>Keywords</h2>
          <span class="card-value">{summary['successful_keywords']}/{summary['configured_keywords']}</span>
        </article>
        <article class="card">
          <h2>Alerts</h2>
          <span class="card-value">{summary['high_rising_count']}</span>
        </article>
        <article class="card">
          <h2>Region</h2>
          <span class="card-value">{escape(site_payload['region'])}</span>
        </article>
      </div>
      <div class="links">
        <a href="{escape(files['csv'])}">Open CSV Report</a>
        <a href="{escape(files['json'])}">Open JSON Snapshot</a>
        <a href="{escape(files['history'])}">Open History JSON</a>
      </div>
    </section>

    <section class="section grid two-col">
      <article class="panel">
        <h2>Run Summary</h2>
        <p class="meta">Generated at {escape(site_payload['generated_at'])}</p>
        <table>
          <tbody>
            <tr><th>Requested timeframe</th><td>{escape(site_payload['requested_timeframe'])}</td></tr>
            <tr><th>Resolved timeframe</th><td>{escape(site_payload['resolved_timeframe'])}</td></tr>
            <tr><th>Configured keywords</th><td>{summary['configured_keywords']}</td></tr>
            <tr><th>Successful keywords</th><td>{summary['successful_keywords']}</td></tr>
            <tr><th>Failed keywords</th><td>{summary['failed_keywords']}</td></tr>
          </tbody>
        </table>
      </article>
      <article class="panel">
        <h2>Recent History</h2>
        <p class="meta">Latest 14 published runs</p>
        <ol class="history-list">
          {_render_history_list(history_entries)}
        </ol>
      </article>
    </section>

    <section class="section panel">
      <h2>High Rising Alerts</h2>
      <p class="meta">Queries whose growth value exceeded the configured threshold.</p>
      {_render_alerts_table(site_payload['high_rising_trends'])}
    </section>

    <section class="section">
      <h2>Group Navigation</h2>
      <p class="meta">The 51 monitored keywords are organized into the themes you defined, so you can jump straight to the section you care about.</p>
      <div class="group-nav">
        {_render_group_nav(group_payload)}
      </div>
    </section>

    <section class="section">
      <h2>Keyword Drilldown</h2>
      <div class="grid">
        {_render_group_sections(group_payload)}
      </div>
    </section>

    <p class="footer">Built from files in {escape(site_payload['source_directory'])}. GitHub Pages serves the latest report from the <code>{escape(STORAGE_CONFIG['site_dir'])}</code> directory.</p>
  </main>
</body>
</html>
"""

def publish_static_site(directory, report_file, high_rising_trends, resolved_timeframe):
    """Publish the latest run into the docs directory for GitHub Pages."""
    site_dir = STORAGE_CONFIG['site_dir']
    docs_data_dir = os.path.join(site_dir, 'data')
    run_date = os.path.basename(directory).replace(STORAGE_CONFIG['data_dir_prefix'], '')
    dated_output_dir = os.path.join(docs_data_dir, run_date)

    os.makedirs(dated_output_dir, exist_ok=True)
    copied_files = _copy_run_assets(directory, dated_output_dir)
    snapshots = _load_keyword_snapshots(directory)

    high_rising_payload = [
        {'keyword': keyword, 'query': query, 'value': value}
        for keyword, query, value in high_rising_trends
    ]
    grouped_snapshots = _build_group_payload(snapshots)

    site_payload = {
        'date': run_date,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'requested_timeframe': TRENDS_CONFIG['timeframe'],
        'resolved_timeframe': resolved_timeframe,
        'region': TRENDS_CONFIG['geo'] or 'Global',
        'source_directory': directory,
        'snapshots': snapshots,
        'grouped_snapshots': grouped_snapshots,
        'high_rising_trends': high_rising_payload,
        'summary': {
            'configured_keywords': len(KEYWORDS),
            'successful_keywords': len(snapshots),
            'failed_keywords': len(KEYWORDS) - len(snapshots),
            'high_rising_count': len(high_rising_payload),
        },
        'files': {
            'csv': f"data/{run_date}/{os.path.basename(report_file)}" if report_file else '',
            'json': f"data/{run_date}/snapshot.json",
            'history': "data/history.json",
            'assets': [f"data/{run_date}/{filename}" for filename in copied_files],
        },
    }

    snapshot_path = os.path.join(dated_output_dir, 'snapshot.json')
    with open(snapshot_path, 'w', encoding='utf-8') as f:
        json.dump(site_payload, f, ensure_ascii=False, indent=2)

    history_entries = []
    if os.path.exists(docs_data_dir):
        for entry in sorted(os.listdir(docs_data_dir), reverse=True):
            entry_dir = os.path.join(docs_data_dir, entry)
            snapshot_file = os.path.join(entry_dir, 'snapshot.json')
            if not os.path.isdir(entry_dir) or not os.path.exists(snapshot_file):
                continue

            with open(snapshot_file, 'r', encoding='utf-8') as f:
                payload = json.load(f)

            history_entries.append({
                'date': payload.get('date', entry),
                'relative_dir': f"data/{entry}",
                'summary': (
                    f"{payload.get('summary', {}).get('successful_keywords', 0)} successful keywords, "
                    f"{payload.get('summary', {}).get('high_rising_count', 0)} alerts"
                ),
            })

    history_path = os.path.join(docs_data_dir, 'history.json')
    with open(history_path, 'w', encoding='utf-8') as f:
        json.dump(history_entries, f, ensure_ascii=False, indent=2)

    index_path = os.path.join(site_dir, 'index.html')
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(_build_site_html(site_payload, history_entries))

    nojekyll_path = os.path.join(site_dir, '.nojekyll')
    with open(nojekyll_path, 'w', encoding='utf-8') as f:
        f.write('')

    logging.info(f"Published static site to {site_dir}")

def get_date_range_timeframe(timeframe):
    """Convert special timeframe formats to date range format
    
    Args:
        timeframe (str): Timeframe string like 'last-2-d' or 'last-3-d'
        
    Returns:
        str: Date range format string like '2024-01-01 2024-01-31'
    """
    if not timeframe.startswith('last-'):
        return timeframe
        
    try:
        # 解析天数
        days = int(timeframe.split('-')[1])
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        # 格式化日期字符串
        return f"{start_date.strftime('%Y-%m-%d')} {end_date.strftime('%Y-%m-%d')}"
    except (ValueError, IndexError):
        logging.warning(f"Invalid timeframe format: {timeframe}, falling back to 'now 1-d'")
        return 'now 1-d'

def process_keywords_batch(keywords_batch, directory, all_results, high_rising_trends, timeframe):
    """处理一批关键词"""
    try:
        logging.info(f"Processing batch of {len(keywords_batch)} keywords")
        logging.info(f"Query parameters: timeframe={timeframe}, geo={TRENDS_CONFIG['geo'] or 'Global'}")
        
        # 使用传入的 timeframe 参数
        results = get_trends_with_retry(keywords_batch, timeframe)
        
        for keyword, data in results.items():
            if data:
                filename = save_related_queries(keyword, data)
                if filename:
                    os.rename(filename, os.path.join(directory, filename))
                
                rising_trends = check_rising_trends(data, keyword)
                if rising_trends:
                    high_rising_trends.extend([(keyword, related_keywords, value) 
                                             for related_keywords, value in rising_trends])
                
                all_results[keyword] = data
        
        return True
    except Exception as e:
        logging.error(f"Error processing batch: {str(e)}")
        return False

@backoff.on_exception(
    backoff.expo,
    Exception,
    max_tries=RATE_LIMIT_CONFIG['max_retries'],
    jitter=backoff.full_jitter
)
def get_trends_with_retry(keywords_batch, timeframe):
    """使用重试机制获取趋势数据"""
    return batch_get_queries(
        keywords_batch,
        timeframe=timeframe,  # 使用传入的 timeframe
        geo=TRENDS_CONFIG['geo'],
        delay_between_queries=random.uniform(
            RATE_LIMIT_CONFIG['min_delay_between_queries'],
            RATE_LIMIT_CONFIG['max_delay_between_queries']
        )
    )

def process_trends():
    """Main function to process trends data"""
    try:
        logging.info("Starting daily trends processing")
        
        # 处理特殊的 timeframe 格式
        timeframe = TRENDS_CONFIG['timeframe']
        actual_timeframe = get_date_range_timeframe(timeframe)
        
        logging.info(f"Using configuration: timeframe={actual_timeframe}, geo={TRENDS_CONFIG['geo'] or 'Global'}")
        directory = create_daily_directory()
        
        all_results = {}
        high_rising_trends = []
        
        # 将关键词分批处理，使用实际的 timeframe
        for i in range(0, len(KEYWORDS), RATE_LIMIT_CONFIG['batch_size']):
            keywords_batch = KEYWORDS[i:i + RATE_LIMIT_CONFIG['batch_size']]
            # 传递实际的 timeframe 到查询函数
            success = process_keywords_batch(
                keywords_batch, 
                directory, 
                all_results, 
                high_rising_trends,
                actual_timeframe
            )
            
            if not success:
                logging.error(f"Failed to process batch starting with keyword: {keywords_batch[0]}")
                continue
            
            # 如果不是最后一批，等待一段时间再处理下一批
            if i + RATE_LIMIT_CONFIG['batch_size'] < len(KEYWORDS):
                wait_time = RATE_LIMIT_CONFIG['batch_interval'] + random.uniform(0, 60)
                logging.info(f"Waiting {wait_time:.1f} seconds before processing next batch...")
                time.sleep(wait_time)

        # Generate and send daily report
        report_file = generate_daily_report(all_results, directory)
        publish_static_site(directory, report_file, high_rising_trends, actual_timeframe)

        if report_file:
            report_body = """
            <h2>Daily Trends Report</h2>
            <p>Please find attached the daily trends report.</p>
            <p>Query Parameters:</p>
            <ul>
            <li>Time Range: {}</li>
            <li>Region: {}</li>
            </ul>
            <p>Summary:</p>
            <ul>
            <li>Total keywords processed: {}</li>
            <li>Successful queries: {}</li>
            <li>Failed queries: {}</li>
            </ul>
            """.format(
                TRENDS_CONFIG['timeframe'],
                TRENDS_CONFIG['geo'] or 'Global',
                len(KEYWORDS),
                len(all_results),
                len(KEYWORDS) - len(all_results)
            )
            if not notification_manager.send_notification(
                subject=f"Daily Trends Report - {datetime.now().strftime('%Y-%m-%d')}",
                body=report_body,
                attachments=[report_file]
            ):
                logging.warning("Failed to send daily report, but data collection completed")
        
        # Send alerts for high rising trends
        if high_rising_trends:
            # 将高趋势分批处理，每批最多10个趋势
            batch_size = 10
            for i in range(0, len(high_rising_trends), batch_size):
                batch_trends = high_rising_trends[i:i + batch_size]
                batch_number = i // batch_size + 1
                total_batches = (len(high_rising_trends) + batch_size - 1) // batch_size
                
                alert_body = f"""
                <h2>📊 High Rising Trends Alert</h2>
                <hr>
                <h3>📌 Query Parameters:</h3>
                <ul>
                    <li>🕒 Time Range: {TRENDS_CONFIG['timeframe']}</li>
                    <li>🌍 Region: {TRENDS_CONFIG['geo'] or 'Global'}</li>
                </ul>
                <h3>📈 Significant Growth Trends:</h3>
                <table border="1" cellpadding="5" style="border-collapse: collapse;">
                    <tr>
                        <th>🔍 Base Keyword</th>
                        <th>🔗 Related Query</th>
                        <th>📈 Growth</th>
                    </tr>
                """
                
                for keyword, related_keywords, value in batch_trends:
                    alert_body += f"""
                    <tr>
                        <td><strong>🎯 {keyword}</strong></td>
                        <td>➡️ {related_keywords}</td>
                        <td align="right" style="color: #28a745;">⬆️ {value}%</td>
                    </tr>
                    """
                
                alert_body += "</table>"
                
                if batch_number < total_batches:
                    alert_body += f"<p><i>This is batch {batch_number} of {total_batches}. More results will follow.</i></p>"
                
                if not notification_manager.send_notification(
                    subject=f"📊 Rising Trends Alert ({batch_number}/{total_batches})",
                    body=alert_body
                ):
                    logging.warning(f"Failed to send alert notification for batch {batch_number}, but data collection completed")
                
                # 添加短暂延迟，避免消息发送过快
                time.sleep(2)
        
        logging.info("Daily trends processing completed successfully")
        return True
    except Exception as e:
        logging.error(f"Error in trends processing: {str(e)}")
        notification_manager.send_notification(
            subject="❌ Error in Trends Processing",
            body=f"<p>An error occurred during trends processing:</p><pre>{str(e)}</pre>"
        )
        return False

def run_scheduler():
    """Run the scheduler"""
    # 从配置中获取小时和分钟
    schedule_hour = SCHEDULE_CONFIG['hour']
    schedule_minute = SCHEDULE_CONFIG.get('minute', 0)  # 默认为0分钟
    
    # 添加随机延迟（如果配置了的话）
    if SCHEDULE_CONFIG.get('random_delay_minutes', 0) > 0:
        random_minutes = random.randint(0, SCHEDULE_CONFIG['random_delay_minutes'])
        total_minutes = schedule_minute + random_minutes
        schedule_hour = (schedule_hour + total_minutes // 60) % 24
        schedule_minute = total_minutes % 60
    
    schedule_time = f"{schedule_hour:02d}:{schedule_minute:02d}"
    
    schedule.every().day.at(schedule_time).do(process_trends)
    
    logging.info(f"Scheduler started. Will run daily at {schedule_time}")
    
    # 如果启动时间接近计划执行时间，等待到下一天
    now = datetime.now()
    scheduled_time = now.replace(hour=schedule_hour, minute=schedule_minute, second=0, microsecond=0)
    
    if now >= scheduled_time:
        logging.info("Current time is past scheduled time, waiting for tomorrow")
        next_run = scheduled_time + timedelta(days=1)
        time.sleep((next_run - now).total_seconds())
    
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description='Google Trends Monitor')
    parser.add_argument('--test', action='store_true', 
                      help='立即运行一次数据收集，而不是等待计划时间')
    parser.add_argument('--keywords', nargs='+',
                      help='测试时要查询的关键词列表，如果不指定则使用配置文件中的关键词')
    args = parser.parse_args()

    notification_method = NOTIFICATION_CONFIG['method']

    if notification_method not in ['none', 'email', 'wechat', 'both']:
        logging.error(f"Unsupported notification method: {notification_method}")
        exit(1)

    if notification_method in ['email', 'both'] and not all([
        EMAIL_CONFIG['sender_email'],
        EMAIL_CONFIG['sender_password'],
        EMAIL_CONFIG['recipient_email']
    ]):
        logging.error("Email notifications are enabled, but email settings are incomplete")
        exit(1)

    if notification_method in ['wechat', 'both'] and not NOTIFICATION_CONFIG['wechat_receiver'].strip():
        logging.error("WeChat notifications are enabled, but TRENDS_WECHAT_RECEIVER is not configured")
        exit(1)
    
    # 如果是测试模式
    if args.test:
        logging.info("Running in test mode...")
        if args.keywords:
            # 临时替换配置文件中的关键词
            global KEYWORDS
            KEYWORDS = args.keywords
            logging.info(f"Using test keywords: {KEYWORDS}")
        process_trends()
    else:
        # 正常的计划任务模式
        run_scheduler() 
