from graph.state import MusicState
from agents.director_agent import DirectorAgent
# from agents.drums_agent import DrumsAgent (等你改完 DrumsAgent 再解开)

# 实例化
director_agent=DirectorAgent()

def run_director_node(state:MusicState):
    """
        Director 节点的执行函数
    """
    print("🎬 Director is planning...")
    user_req = state["user_request"]
    # 调用业务agent
    bp_obj=director_agent.generate_blueprint(user_req)
    return {
        "blueprint": bp_obj.model_dump(),
        "round": 1, # 初始化轮次
        "errors": [] # 清空错误
    }

# 未来 Drums 节点的预告：
# def run_drums_node(state: MusicState):
#     print("🥁 Drums generating...")
#     # 1. 从 State 取 blueprint
#     bp_data = state["blueprint"]
#     # 2. 调 Agent
#     drums_out = drums_agent.generate(bp_data, ...)
#     # 3. 返回更新量 (利用 merge_dicts 只有 drums 被更新，不会覆盖别人)
#     return {
#         "tracks": {"drums": drums_out}
#     }