from __future__ import annotations

import math
import os
import sys
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, redirect, render_template_string, request, url_for
from sqlalchemy import create_engine
from sqlalchemy.orm import class_mapper, sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chanakya.config import get_database_url, load_local_env
from chanakya.model import (
    AgentProfileModel,
    AppEventModel,
    ChatMessageModel,
    ChatSessionModel,
)


def get_sync_url(database_url: str) -> str:
    if "+asyncpg" in database_url:
        return database_url.replace("+asyncpg", "")
    return database_url


def create_viewer_app() -> Flask:
    return Flask(__name__)


load_local_env()
SYNC_DB_URL = get_sync_url(get_database_url())
engine = create_engine(SYNC_DB_URL, future=True)
Session = sessionmaker(bind=engine)
app = create_viewer_app()

MODELS = {
    "ChatSessionModel": ChatSessionModel,
    "ChatMessageModel": ChatMessageModel,
    "AppEventModel": AppEventModel,
    "AgentProfileModel": AgentProfileModel,
}


def serialize_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def get_model_columns(mapper):
    columns = []
    for attr in mapper.column_attrs:
        if not attr.columns:
            continue
        column = attr.columns[0]
        columns.append(
            {
                "header": column.name,
                "key": attr.key,
                "is_json": "JSON" in str(column.type),
            }
        )
    return columns


VIEWER_HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Chanakya DB Viewer</title>
    <style>
      body { font-family: sans-serif; padding: 20px; background-color: #1a1a1a; color: #e0e0e0; }
      nav { margin-bottom: 20px; display: flex; align-items: center; gap: 15px; flex-wrap: wrap; }
      nav a { text-decoration: none; color: #66b0ff; margin-right: 10px;}
      nav a:hover { text-decoration: underline; }
      
      .action-bar { margin-bottom: 10px; padding: 10px; background: #252525; border: 1px solid #444; border-radius: 4px; display: flex; gap: 10px; align-items: center; justify-content: space-between; flex-wrap: wrap; }
      .action-group { display: flex; gap: 10px; align-items: center; }
      
      table { border-collapse: collapse; width: 100%; border: 1px solid #444; table-layout: fixed;}
      th, td { border: 1px solid #444; padding: 8px; text-align: left; vertical-align: top; max-width: 300px; word-wrap: break-word; overflow-x: auto; }
      th { background-color: #333; color: #fff; cursor: pointer; user-select: none; position: relative;}
      th:hover { background-color: #444; }
      th.sort-asc::after { content: " ▲"; color: #00C9FF; position: absolute; right: 5px; }
      th.sort-desc::after { content: " ▼"; color: #00C9FF; position: absolute; right: 5px; }
      
      tr:nth-child(even) { background-color: #252525; }
      tr.selected { background-color: #3a4a5a !important; }
      
      .btn { padding: 5px 15px; cursor: pointer; border-radius: 4px; border: none; font-weight: bold; text-decoration: none; display: inline-block; color: #222; background: #ccc;}
      .btn:hover { background: #ddd; }
      .btn-primary { background-color: #007bff; color: white; }
      .btn-primary:hover { background-color: #0056b3; }
      .btn-danger { background-color: #d9534f; color: white; }
      .btn-danger:hover { background-color: #c9302c; }
      .btn-danger:disabled { background-color: #555; cursor: not-allowed; color: #888; }
      .btn:disabled, .btn.disabled { background-color: #555; cursor: not-allowed; color: #888; pointer-events: none; }
      
      select, input { padding: 5px; border-radius: 4px; border: 1px solid #555; background: #333; color: white; }
      
      pre { white-space: pre-wrap; margin: 0; font-size: 0.85em; max-height: 200px; overflow-y: auto;}
      input[type="checkbox"] { transform: scale(1.2); cursor: pointer; }
      
      .pagination { display: flex; gap: 5px; align-items: center; }
    </style>
  </head>
  <body>
      <h1>Chanakya Database Viewer</h1>
    <nav>
        <a href="/db-viewer">Home</a>
        {% for name in models %}
            <a href="/db-viewer/{{ name }}" style="{% if name == current_model %}font-weight: bold; color: white; border-bottom: 2px solid #00C9FF;{% endif %}">{{ name }}</a>
        {% endfor %}
    </nav>
    
    {% if current_model %}
        <h2>{{ current_model }}</h2>
        
        <div class="action-bar">
            <div class="action-group">
                <span>Selected: <strong id="selected-count">0</strong></span>
                <form id="batch-delete-form" action="/db-viewer/{{ current_model }}/delete_batch" method="post" onsubmit="return confirm('Delete selected items?');">
                    <input type="hidden" name="ids" id="batch-delete-ids">
                    <button type="submit" class="btn btn-danger" id="btn-delete-batch" disabled>Delete Selected</button>
                </form>
            </div>
            
            <div class="action-group pagination">
                <form action="" method="get" id="limit-form">
                    <label>Show: 
                        <select name="limit" onchange="document.getElementById('limit-form').submit()">
                            <option value="10" {% if limit == 10 %}selected{% endif %}>10</option>
                            <option value="25" {% if limit == 25 %}selected{% endif %}>25</option>
                            <option value="50" {% if limit == 50 %}selected{% endif %}>50</option>
                            <option value="100" {% if limit == 100 %}selected{% endif %}>100</option>
                        </select>
                    </label>
                    <input type="hidden" name="page" value="1"> 
                </form>
                
                <span>Total: {{ total_count }}</span>
                
                <form action="" method="get" style="display: inline-flex; align-items: center; gap: 5px;">
                    <input type="hidden" name="limit" value="{{ limit }}">
                    <label>Page <input type="number" name="page" value="{{ page }}" min="1" max="{{ total_pages }}" style="width: 50px; text-align: center;"> of {{ total_pages }}</label>
                    <button type="submit" class="btn" style="padding: 2px 8px;">Go</button>
                </form>
                
                <a href="{{ url_for('db_viewer', model_name=current_model, page=page-1, limit=limit) }}" class="btn {% if page <= 1 %}disabled{% endif %}">Previous</a>
                <a href="{{ url_for('db_viewer', model_name=current_model, page=page+1, limit=limit) }}" class="btn {% if page >= total_pages %}disabled{% endif %}">Next</a>
            </div>
        </div>

        <div style="overflow-x: auto;">
            <table id="data-table">
                <thead>
                    <tr>
                        <th onclick="toggleAll(this)" style="width: 40px; text-align: center;" title="Select All">
                            <input type="checkbox" id="select-all">
                        </th>
                        {% for header in headers %}
                            <th onclick="sortTable({{ loop.index }})">{{ header }}</th>
                        {% endfor %}
                        <th style="width: 80px;">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {% for row in rows %}
                        <tr onclick="rowClick(event, '{{ row['_pk_value'] }}')">
                            <td style="text-align: center;" onclick="event.stopPropagation()">
                                <input type="checkbox" class="row-select" value="{{ row['_pk_value'] }}" onchange="updateSelection(event)">
                            </td>
                            {% for col in columns %}
                                <td>
                                    {% if col in json_cols %}
                                        <pre>{{ row[col] | tojson(indent=2) }}</pre>
                                    {% else %}
                                        {{ row[col] }}
                                    {% endif %}
                                </td>
                            {% endfor %}
                            <td onclick="event.stopPropagation()">
                                <form action="/db-viewer/{{ current_model }}/delete/{{ row['_pk_value'] }}" method="post" onsubmit="return confirm('Delete this item?');">
                                    <button type="submit" class="btn btn-danger" style="padding: 2px 8px; font-size: 0.8em;">Delete</button>
                                </form>
                            </td>
                        </tr>
                    {% endfor %}
                    {% if not rows %}
                         <tr><td colspan="100%" style="text-align:center; padding: 20px;">No data found.</td></tr>
                    {% endif %}
                </tbody>
            </table>
        </div>
        
        <script>
            let lastChecked = null;
            const checkboxes = document.querySelectorAll('.row-select');
            const selectAll = document.getElementById('select-all');
            const deleteBtn = document.getElementById('btn-delete-batch');
            const countSpan = document.getElementById('selected-count');
            const hiddenInput = document.getElementById('batch-delete-ids');

            function updateSelection(e) {
                if (e && e.shiftKey && lastChecked) {
                    let start = Array.from(checkboxes).indexOf(this);
                    let end = Array.from(checkboxes).indexOf(lastChecked);
                    
                    const min = Math.min(start, end);
                    const max = Math.max(start, end);
                    
                    for (let i = min; i <= max; i++) {
                        checkboxes[i].checked = lastChecked.checked;
                        updateRowStyle(checkboxes[i]);
                    }
                }
                
                lastChecked = this;
                if(e && e.target === selectAll) return;
                
                updateUI();
                updateRowStyle(this);
            }
            
            function updateRowStyle(box) {
                const row = box.closest('tr');
                if (box.checked) row.classList.add('selected');
                else row.classList.remove('selected');
            }

            checkboxes.forEach(chk => {
                chk.addEventListener('click', function(e) {
                    if (e.shiftKey && lastChecked) {
                         let start = Array.from(checkboxes).indexOf(this);
                         let end = Array.from(checkboxes).indexOf(lastChecked);
                         let inBetween = Array.from(checkboxes).slice(Math.min(start, end), Math.max(start, end) + 1);
                         inBetween.forEach(box => {
                             box.checked = lastChecked.checked;
                             updateRowStyle(box);
                         });
                    }
                    lastChecked = this;
                    updateUI();
                    updateRowStyle(this);
                });
            });

            function toggleAll(header) {
                const state = selectAll.checked;
                checkboxes.forEach(cb => {
                    cb.checked = state;
                    updateRowStyle(cb);
                });
                updateUI();
            }
            
            function rowClick(e, id) {
                if(e.target.tagName === 'BUTTON' || e.target.tagName === 'A' || e.target.tagName === 'INPUT' || e.target.tagName === "SELECT") return;
                
                const tr = e.currentTarget;
                const cb = tr.querySelector('.row-select');
                if(cb) {
                    cb.checked = !cb.checked;
                    updateRowStyle(cb);
                    lastChecked = cb;
                    updateUI();
                }
            }

            function updateUI() {
                const checked = document.querySelectorAll('.row-select:checked');
                const ids = Array.from(checked).map(cb => cb.value);
                
                countSpan.innerText = ids.length;
                hiddenInput.value = ids.join(',');
                deleteBtn.disabled = ids.length === 0;
            }

            // Simple client-side sorting for current page
            function sortTable(n) {
                var table, rows, switching, i, x, y, shouldSwitch, dir, switchcount = 0;
                table = document.getElementById("data-table");
                switching = true;
                dir = "asc";
                
                document.querySelectorAll('th').forEach(th => {
                    th.classList.remove('sort-asc', 'sort-desc');
                });
                
                while (switching) {
                    switching = false;
                    rows = table.rows;
                    
                    for (i = 1; i < (rows.length - 1); i++) {
                        shouldSwitch = false;
                        x = rows[i].getElementsByTagName("TD")[n];
                        y = rows[i + 1].getElementsByTagName("TD")[n];
                        
                        var xContent = x.innerText.toLowerCase();
                        var yContent = y.innerText.toLowerCase();
                        
                        var xNum = parseFloat(xContent);
                        var yNum = parseFloat(yContent);
                        if (!isNaN(xNum) && !isNaN(yNum)) {
                            xContent = xNum;
                            yContent = yNum;
                        }

                        if (dir == "asc") {
                            if (xContent > yContent) {
                                shouldSwitch = true;
                                break;
                            }
                        } else if (dir == "desc") {
                            if (xContent < yContent) {
                                shouldSwitch = true;
                                break;
                            }
                        }
                    }
                    if (shouldSwitch) {
                        rows[i].parentNode.insertBefore(rows[i + 1], rows[i]);
                        switching = true;
                        switchcount++;
                    } else {
                        if (switchcount == 0 && dir == "asc") {
                            dir = "desc";
                            switching = true;
                        }
                    }
                }
                const th = table.rows[0].getElementsByTagName("TH")[n];
                th.classList.add(dir == 'asc' ? 'sort-asc' : 'sort-desc');
            }
        </script>
    {% else %}
        <p>Select a table to view data.</p>
    {% endif %}
  </body>
</html>
"""


@app.teardown_appcontext
def shutdown_session(exception=None):
    pass


def coerce_pk_value(model, pk_name, pk_value):
    column = model.__table__.columns[pk_name]
    python_type = getattr(column.type, "python_type", str)
    try:
        return python_type(pk_value)
    except (TypeError, ValueError, NotImplementedError):
        return pk_value


@app.route("/db-viewer/<model_name>/delete/<pk_value>", methods=["POST"])
def delete_item(model_name, pk_value):
    if model_name in MODELS:
        model = MODELS[model_name]
        mapper = class_mapper(model)
        pk_keys = [key.name for key in mapper.primary_key]
        if pk_keys:
            pk_name = pk_keys[0]
            pk_value = coerce_pk_value(model, pk_name, pk_value)

            session = Session()
            try:
                obj = session.query(model).filter(getattr(model, pk_name) == pk_value).first()
                if obj:
                    session.delete(obj)
                    session.commit()
            except Exception as e:
                session.rollback()
                return f"Error deleting: {e}", 500
            finally:
                session.close()

        return redirect(request.referrer or f"/db-viewer/{model_name}")
    return "Model not found", 404


@app.route("/db-viewer/<model_name>/delete_batch", methods=["POST"])
def delete_batch(model_name):
    if model_name in MODELS:
        model = MODELS[model_name]
        ids_str = request.form.get("ids", "")
        if not ids_str:
            return redirect(request.referrer or f"/db-viewer/{model_name}")

        ids = ids_str.split(",")
        mapper = class_mapper(model)
        pk_keys = [key.name for key in mapper.primary_key]

        if pk_keys:
            pk_name = pk_keys[0]
            ids = [coerce_pk_value(model, pk_name, item) for item in ids]
            session = Session()
            try:
                session.query(model).filter(getattr(model, pk_name).in_(ids)).delete(
                    synchronize_session=False
                )
                session.commit()
            except Exception as e:
                session.rollback()
                return f"Error batch deleting: {e}", 500
            finally:
                session.close()

        return redirect(request.referrer or f"/db-viewer/{model_name}")
    return "Model not found", 404


@app.route("/db-viewer")
@app.route("/db-viewer/<model_name>")
def db_viewer(model_name=None):
    if model_name and model_name in MODELS:
        model = MODELS[model_name]

        page = request.args.get("page", 1, type=int)
        limit = request.args.get("limit", 10, type=int)
        if page < 1:
            page = 1
        if limit < 1:
            limit = 10
        if limit > 500:
            limit = 500

        mapper = class_mapper(model)
        column_defs = get_model_columns(mapper)
        columns = [column["key"] for column in column_defs]
        headers = [column["header"] for column in column_defs]
        pk_keys = [key.name for key in mapper.primary_key]
        pk_name = pk_keys[0] if pk_keys else "id"

        json_cols = [column["key"] for column in column_defs if column["is_json"]]

        session = Session()
        try:
            query = session.query(model)
            total_count = query.count()
            total_pages = math.ceil(total_count / limit)
            if total_pages < 1:
                total_pages = 1
            if page > total_pages:
                page = total_pages

            items = query.limit(limit).offset((page - 1) * limit).all()

            rows = []
            for item in items:
                row = {}
                for column in column_defs:
                    value = serialize_value(getattr(item, column["key"]))
                    row[column["key"]] = value
                row["_pk_value"] = serialize_value(getattr(item, pk_name))
                rows.append(row)
        finally:
            session.close()

        return render_template_string(
            VIEWER_HTML,
            models=MODELS.keys(),
            current_model=model_name,
            columns=columns,
            headers=headers,
            rows=rows,
            json_cols=json_cols,
            page=page,
            limit=limit,
            total_count=total_count,
            total_pages=total_pages,
        )

    return render_template_string(VIEWER_HTML, models=MODELS.keys(), current_model=None)


@app.route("/")
def index():
    return redirect("/db-viewer")


if __name__ == "__main__":
    print("Starting Chanakya DB Viewer on http://localhost:5014/")
    app.run(host="0.0.0.0", port=5014, debug=True)
