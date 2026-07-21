import sys, os, json, time

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)
import visualize


def generate_chart(data_context: str) -> dict:
    try:
        plan_path = os.path.join(_BASE_DIR, "data", "plan.json")

        if not os.path.exists(plan_path):
            return {"error": "暂无训练计划数据，请先生成训练计划"}

        with open(plan_path, "r", encoding="utf-8") as f:
            plan_data = json.load(f)

        structured_plan = plan_data.get("plan", [])
        if not structured_plan:
            return {"error": "暂无训练计划数据，请先生成训练计划"}

        timestamp = int(time.time())
        charts_dir = os.path.join(_BASE_DIR, "data", "charts")
        os.makedirs(charts_dir, exist_ok=True)

        output_path = os.path.join(charts_dir, f"pace_chart_{timestamp}.png")
        visualize.plot_pace_chart(structured_plan, output_path)

        desc = data_context.strip() if data_context and data_context.strip() else "已生成配速图表，展示训练计划的配速和距离分布"

        return {
            "chart_url": f"/chart/pace_chart_{timestamp}.png",
            "description": desc,
        }

    except Exception as e:
        return {"error": str(e)}