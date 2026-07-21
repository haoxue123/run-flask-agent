"""
工作流状态机
============

定义教练对话的五态状态机，控制对话流程的递进和转换。

五态模型：
    chat   → 闲聊（问候、非跑步话题）
    assess → 评估（分析跑者数据）
    advise → 建议（给出训练建议）
    plan   → 计划（生成/修改训练计划）
    track  → 跟踪（检查计划执行进度）

状态转换由 LLM 意图分类驱动，非法转换回退到 chat。
"""

# ============================================================
# 状态常量
# ============================================================

STATE_CHAT = "chat"
STATE_ASSESS = "assess"
STATE_ADVISE = "advise"
STATE_PLAN = "plan"
STATE_TRACK = "track"

ALL_STATES = (STATE_CHAT, STATE_ASSESS, STATE_ADVISE, STATE_PLAN, STATE_TRACK)

# 状态中文标签
STATE_LABELS = {
    STATE_CHAT: "闲聊",
    STATE_ASSESS: "评估",
    STATE_ADVISE: "建议",
    STATE_PLAN: "计划",
    STATE_TRACK: "跟踪",
}

# ============================================================
# 合法转换表
# ============================================================

# 键 = 当前状态，值 = 可转换到的目标状态集合
_TRANSITIONS = {
    STATE_CHAT:   {STATE_CHAT, STATE_ASSESS, STATE_ADVISE, STATE_PLAN},
    STATE_ASSESS: {STATE_CHAT, STATE_ASSESS, STATE_ADVISE, STATE_TRACK},
    STATE_ADVISE: {STATE_CHAT, STATE_ASSESS, STATE_ADVISE, STATE_PLAN},
    STATE_PLAN:   {STATE_CHAT, STATE_ADVISE, STATE_PLAN, STATE_TRACK},
    STATE_TRACK:  {STATE_CHAT, STATE_ASSESS, STATE_ADVISE, STATE_TRACK},
}

# ============================================================
# 状态转换
# ============================================================

def get_next_state(current_state, intent):
    """
    根据当前状态和用户意图返回下一个状态。

    参数：
        current_state: 当前状态（chat/assess/advise/plan/track）
        intent:        LLM 分类的意图（chat/assess/advise/plan/track）

    返回：
        str，下一个状态。非法转换时回退到 chat。
    """
    if current_state not in _TRANSITIONS:
        return STATE_CHAT

    if intent not in ALL_STATES:
        return STATE_CHAT

    if intent in _TRANSITIONS[current_state]:
        return intent

    # 非法转换：回退到 chat
    return STATE_CHAT


# ============================================================
# 状态 Prompt 片段
# ============================================================

def get_state_prompt(state):
    """
    根据当前状态返回对应的 system prompt 指令片段。

    返回：
        str，追加到 system prompt 的指令文本
    """
    prompts = {
        STATE_CHAT: """
【当前模式：闲聊】
你正在与跑者进行轻松对话。友好问候，回答一般性问题。
如果跑者提到训练相关话题，自然地引导他们深入讨论——可以问"想看看你的训练数据吗？"或"有什么训练问题想问我吗？"
保持轻松愉快的语气。""",

        STATE_ASSESS: """
【当前模式：数据评估】
你正在为跑者分析训练数据。请基于提供的跑者数据、训练科学指标（ACWR、TRIMP、就绪度）进行专业评估。
明确指出：
- 训练负荷是否合理（ACWR 是偏高还是偏低）
- 近期的进步或退步趋势
- 需要关注的风险点（如配速过快、心率偏高）
用数据说话，给出具体数字。""",

        STATE_ADVISE: """
【当前模式：训练建议】
你正在为跑者提供具体的训练建议。请：
- 给出可操作的具体建议（不是泛泛而谈）
- 引用运动科学知识（如有知识库检索结果，请基于知识回答）
- 考虑跑者的个人偏好和限制（参考用户画像）
- 建议应包含：具体配速、距离、频率、注意事项
- 回答末尾标注知识来源。""",

        STATE_PLAN: """
【当前模式：计划生成】
你正在帮助跑者制定训练计划。请：
- 引导用户明确计划参数：训练天数、目标比赛、可用训练时间
- 如果用户已确认参数，建议他们到"训练计划"页面使用"生成计划"功能
- 可以给出计划的大致框架建议（如每周几次训练、LSD 安排在何时）
- 提醒用户：具体计划请到训练计划页面生成。""",

        STATE_TRACK: """
【当前模式：进度跟踪】
你正在检查跑者的训练计划执行情况。请：
- 对比计划配速 vs 实际配速，指出偏差
- 检查计划完成率（已完成/总训练日）
- 如果完成率低或偏差大，分析原因（太忙？受伤？强度太高？）
- 给出调整建议：是否需要降低强度、增加恢复日、调整计划
- 鼓励跑者坚持，肯定已完成的训练。""",
    }

    return prompts.get(state, prompts[STATE_CHAT])


def get_state_label(state):
    """返回状态的中文显示名。"""
    return STATE_LABELS.get(state, "未知")


def get_default_state():
    """返回默认初始状态。"""
    return STATE_CHAT