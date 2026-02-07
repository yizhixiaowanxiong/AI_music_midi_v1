from graph.nodes import run_director_node
from graph.state import MusicState  # 引入你的 TypedDict 做类型提示（可选）


def test_director_node_success():
    """
    测试 Director 节点能否正常生成合法的 State 更新
    """
    # 1. 模拟初始 State (模拟 workflow 传进来的数据)
    fake_state = {
        "user_request": "写一首 BPM 128 的 Cyberpunk 风格 techno，要有强烈的 bassline",
        "blueprint": None,
        "tracks": {},
        "errors": []
    }

    # 2. 运行节点 (这里会真实调用 LLM，耗时较长)
    print("\n🚀 正在调用 Director Agent...")
    update = run_director_node(fake_state)

    # 3. 验证结果 (Assert)
    # 检查是否返回了 blueprint
    assert "blueprint" in update, "返回结果必须包含 blueprint 字段"

    bp = update["blueprint"]
    print(f"\n✅ 成功生成 Blueprint: {bp.get('song_name', 'No Name')}")

    # 检查核心字段是否存在 (Pydantic 其实已经保过了，这里是双保险)
    assert bp["bpm"] == 128
    assert "Cyberpunk" in bp.get("style_description", "") or "Techno" in bp.get("style_description", "")
    assert len(bp["sections"]) > 0

    # 检查是否清空了错误
    assert update["errors"] == []