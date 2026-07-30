"""
RAG 知识库模块
==============

为教练对话提供跑步运动科学知识的检索增强生成能力。

核心流程：
1. 读取 data/knowledge/*.md 知识文档
2. 按 ## 二级标题分块（每块 500-800 字，重叠 100 字）
3. 使用 sentence-transformers 生成嵌入向量
4. 存入 ChromaDB 向量数据库（持久化到 data/chroma_db/）
5. 检索时返回最相关片段 + 来源标注

依赖：chromadb, sentence-transformers（可选，降级到 ChromaDB 内置嵌入）
"""

import os
import re
import threading

# ============================================================
# 路径常量
# ============================================================

_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_KNOWLEDGE_DIR = os.path.join(_PROJECT_DIR, "data", "knowledge")
_DEFAULT_DB_DIR = os.path.join(_PROJECT_DIR, "data", "chroma_db")

# 分块参数
_CHUNK_SIZE = 600      # 每块目标字数
_CHUNK_OVERLAP = 100   # 相邻块重叠字数

# 格式化参数
_CONTEXT_MAX_CHARS = 800  # 每条知识片段的上下文最大字符数

# Sentence-Transformers 模型名（支持中文的多语言模型）
_EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


# ============================================================
# 嵌入函数：优先 SiliconFlow API(bge-m3)，降级 sentence-transformers，最后 ChromaDB 内置
# ============================================================

_embedding_fn = None
_embedding_fn_tag = None   # 标识当前嵌入函数，用于检测索引是否需要重建
_embedding_fn_lock = threading.Lock()


def _get_embedding_function():
    """
    获取嵌入函数，优先级：
    1. SiliconFlow API (BAAI/bge-m3) — 需 SILICONFLOW_API_KEY，中文效果最佳，与 mem0 一致
    2. sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2) — 本地模型，需下载权重
    3. ChromaDB 内置嵌入 — 最后兜底（英文模型，中文效果差）
    线程安全：使用锁保证单例初始化。
    """
    global _embedding_fn, _embedding_fn_tag
    if _embedding_fn is not None:
        return _embedding_fn

    with _embedding_fn_lock:
        if _embedding_fn is not None:
            return _embedding_fn

        # 确保环境变量已加载
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass

        # 1. 尝试 SiliconFlow API (BAAI/bge-m3)
        sf_key = os.getenv("SILICONFLOW_API_KEY")
        if sf_key:
            try:
                from chromadb.utils import embedding_functions
                _embedding_fn = embedding_functions.OpenAIEmbeddingFunction(
                    api_key=sf_key,
                    api_base="https://api.siliconflow.cn/v1",
                    model_name="BAAI/bge-m3",
                )
                # 验证可用性：嵌入一个测试文本
                test_result = _embedding_fn(["测试"])
                if test_result and len(test_result[0]) > 0:
                    _embedding_fn_tag = "siliconflow_bge_m3"
                    print("[RAG] 使用嵌入模型：SiliconFlow BAAI/bge-m3（API）")
                    return _embedding_fn
                else:
                    raise RuntimeError("嵌入返回空结果")
            except Exception as e:
                print(f"[RAG] SiliconFlow API 嵌入不可用（{e}），尝试下一选项")
                _embedding_fn = None

        # 2. 尝试 sentence-transformers
        try:
            from chromadb.utils import embedding_functions
            _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=_EMBEDDING_MODEL
            )
            _embedding_fn_tag = "sentence_transformers"
            print(f"[RAG] 使用嵌入模型：{_EMBEDDING_MODEL}")
            return _embedding_fn
        except Exception as e:
            print(f"[RAG] sentence-transformers 不可用（{e}），降级到 ChromaDB 内置嵌入")

        # 3. 降级：ChromaDB 内置嵌入
        try:
            import chromadb
            _embedding_fn = chromadb.utils.embedding_functions.DefaultEmbeddingFunction()
            _embedding_fn_tag = "chromadb_default"
            print("[RAG] 使用降级嵌入：ChromaDB DefaultEmbeddingFunction")
            return _embedding_fn
        except Exception as e:
            raise RuntimeError(
                f"[RAG] 无法初始化任何嵌入函数。请安装 chromadb：pip install chromadb\n"
                f"  错误详情：{e}"
            )


# ============================================================
# 分块函数
# ============================================================

def _truncate_at_sentence_boundary(text, max_chars):
    """
    按句子边界截断文本，避免在句子中间切断。

    优先在 。！？\n 处截断，若找不到则在最后一个空格处截断，
    若还找不到则硬截断。
    """
    if len(text) <= max_chars:
        return text

    # 在 max_chars 范围内找最后一个句子边界
    segment = text[:max_chars]
    for delimiter in ["。", "！", "？", "\n", ". ", "! ", "? "]:
        idx = segment.rfind(delimiter)
        if idx > max_chars * 0.5:  # 至少保留一半内容
            return text[:idx + len(delimiter)]

    # 找最后一个空格
    idx = segment.rfind(" ")
    if idx > max_chars * 0.5:
        return text[:idx]

    return segment


def _split_markdown(filepath):
    """
    将 Markdown 文件按 ## 二级标题切分为文本块。

    策略：
    - 一级标题（#）作为文档元信息
    - 用 ## 作为分块边界
    - 每个 ## 节若超过 _CHUNK_SIZE 字，则进一步按段落切分
    - 相邻块之间保留 _CHUNK_OVERLAP 字的重叠（按句子边界）

    返回：
        list[str]，每个字符串为一个文本块（含标题层级）
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    filename = os.path.basename(filepath)
    doc_title = filename

    # 提取一级标题
    h1_match = re.match(r"^#\s+(.+)$", content, re.MULTILINE)
    if h1_match:
        doc_title = h1_match.group(1).strip()

    # 按 ## 切分（保留 ## 标题）
    sections = re.split(r"\n(?=##\s)", content)

    chunks = []
    for section in sections:
        section = section.strip()
        if not section:
            continue

        # 给每段加上文档标题前缀
        text = f"【{doc_title}】\n{section}"

        char_count = len(text)
        if char_count <= _CHUNK_SIZE:
            chunks.append(text)
        else:
            # 长段落按段落切分
            paragraphs = text.split("\n\n")
            buffer = ""
            for para in paragraphs:
                if len(buffer) + len(para) + 2 <= _CHUNK_SIZE:
                    buffer += ("\n\n" if buffer else "") + para
                else:
                    if buffer:
                        chunks.append(buffer.strip())
                    buffer = para
                    # 如果单段超过 chunk_size，按句子切分
                    while len(buffer) > _CHUNK_SIZE:
                        split_point = _CHUNK_SIZE
                        # 在 _CHUNK_SIZE 附近找句子边界
                        for delim in ["。", "！", "？", "\n", ". ", "! "]:
                            idx = buffer[:split_point].rfind(delim)
                            if idx > _CHUNK_SIZE * 0.5:
                                split_point = idx + len(delim)
                                break
                        chunks.append(buffer[:split_point].strip())
                        buffer = buffer[split_point:].strip()
            if buffer:
                chunks.append(buffer.strip())

    # 添加重叠：每个 chunk 从前一个 chunk 末尾取 _CHUNK_OVERLAP 字（按句子边界）
    if _CHUNK_OVERLAP > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            if len(prev) > _CHUNK_OVERLAP:
                # 从 prev 末尾取重叠文本，按句子边界
                overlap_start = max(0, len(prev) - _CHUNK_OVERLAP)
                # 向前扩展到句子边界
                for delim in ["。", "！", "？", "\n", ". "]:
                    idx = prev[:overlap_start].rfind(delim)
                    if idx > overlap_start - 50:  # 不超过 overlap 前 50 字
                        overlap_start = idx + len(delim)
                        break
                overlap_text = prev[overlap_start:]
                overlapped.append(overlap_text + "\n\n" + chunks[i])
            else:
                overlapped.append(chunks[i])
        chunks = overlapped

    return chunks


# ============================================================
# 知识文档元数据解析（H1 注释块 → 结构化字段）
# ============================================================
#
# 知识文档在 H1 之后用 HTML 注释块写入元数据，例如：
#   <!--
#   meta:
#     category: 公开指南
#     author: WHO
#     year: 2020
#     authority_level: 高
#     source_type: guideline
#     lang: zh
#   -->
# 解析后写入 ChromaDB metadata，使检索可按 category 过滤、按
# authority_level 加权（冲突时高权威优先）。

_DEFAULT_META = {
    "category": "自编",
    "author": "团队自编",
    "year": "未知",
    "authority_level": "中",
    "source_type": "note",
    "lang": "zh",
}

# 权威度 → 加权系数（用于检索重排，冲突仲裁依据）
_AUTH_WEIGHT = {"高": 1.0, "中": 0.65, "低": 0.35}


def _parse_meta(filepath):
    """
    从 Markdown 文件解析 H1 后的 <!-- meta: ... --> 注释块。

    返回 dict；若文件中无 meta 块，返回空 dict（调用方用 _DEFAULT_META 兜底）。
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}

    m = re.search(r"<!--\s*meta:(.*?)-->", text, re.S)
    if not m:
        return {}

    meta = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            meta[key] = value
    return meta


def rerank_with_authority(results, boost=0.2, summary_boost=0.04):
    """
    按「相似度 + 权威度 + 摘要」融合重排检索结果（冲突仲裁核心）。

    - results: retrieve_knowledge 的原始输出列表（每条含 score / authority_level / is_summary）
    - boost: 权威度融合权重（0=只看相似度，1=只看权威度）；默认 0.2
    - summary_boost: 中文摘要块额外加成（0=不加成）；默认 0.04，用于提升中文召回
    - 返回：重排后的新列表，每条额外带 fused_score 字段

    规则：高权威源在相似度接近时胜出；相似度差距大时仍以相似度为主；
          is_summary=true 的摘要块给予小幅加成，优先呈现文档要点。
    """
    if not results:
        return results

    out = []
    for r in results:
        lvl = r.get("authority_level", "中")
        aw = _AUTH_WEIGHT.get(lvl, 0.65)
        base = r.get("score", 0.0)
        sb = summary_boost if str(r.get("is_summary", "false")).lower() in ("true", "1") else 0.0
        fused = (1.0 - boost) * base + boost * aw + sb
        item = dict(r)
        item["authority_level"] = lvl
        item["is_summary"] = str(r.get("is_summary", "false")).lower() in ("true", "1")
        item["fused_score"] = round(min(1.0, fused), 4)
        out.append(item)

    out.sort(key=lambda x: x["fused_score"], reverse=True)
    return out


# ============================================================
# 治理文件识别 + 文档标题/摘要解析
# ============================================================

# 治理/运营类文档：不入库为「知识」，避免被当作权威知识引用
_GOVERNANCE_FILES = {"OPERATIONS.md"}


def _is_governance(filename):
    """文件名以 _ 开头（如 _licenses.md）或为已知运营文档 → 视为治理文件，跳过入库。"""
    return filename.startswith("_") or filename in _GOVERNANCE_FILES


def _doc_title(filepath):
    """提取一级标题作为文档标题；缺省回退为文件名。"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return os.path.basename(filepath)
    m = re.match(r"^#\s+(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else os.path.basename(filepath)


def _extract_summary(filepath):
    """
    提取文档的「概述与中文要点摘要」节正文，用于构建中文摘要层。
    无该节时返回空串（不新增摘要块）。
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    m = re.search(r"##\s*概述与中文要点摘要\s*\n(.*?)(?=\n##\s|\Z)", text, re.S)
    return m.group(1).strip() if m else ""


# ============================================================
# 索引构建与加载
# ============================================================

_collection = None
_build_lock = threading.Lock()

# ============================================================
# 检索结果缓存（自失效 LRU）
# ------------------------------------------------------------
# 热路径 retrieve_knowledge() 每次都会对 query 重新嵌入 + 向量检索 +
# 重排。教练对话中，同一用户消息的重试 / 完全相同的追问会重复触发，
# 缓存可避免重复的嵌入与检索开销。
#
# 失效策略：缓存与索引版本号 _retrieve_cache_version 绑定。任何会
# 改变索引内容的操作（reset_index / build_index）都会使版本号自增并
# 清空缓存，从而保证缓存结果永远与当前索引一致，无需调用方感知。
# ============================================================

_retrieve_cache = {}            # (query, top_k, category, authority_boost) -> (version, results)
_retrieve_cache_order = []     # LRU 访问顺序（队首最旧）
_retrieve_cache_max = 128       # 最大缓存条目数
_retrieve_cache_version = 0     # 索引版本号：重建时自增
_retrieve_cache_lock = threading.Lock()


def _invalidate_retrieve_cache():
    """使检索缓存整体失效（索引重建时调用）。"""
    global _retrieve_cache_version
    _retrieve_cache_version += 1
    _retrieve_cache.clear()
    _retrieve_cache_order.clear()


def _is_index_valid(db_dir):
    """检查 ChromaDB 索引目录是否有效。"""
    if not os.path.isdir(db_dir):
        return False
    try:
        entries = os.listdir(db_dir)
    except OSError:
        return False
    return any(
        f.endswith(".bin") or f.endswith(".parquet") or f == "chroma.sqlite3"
        for f in entries
    )


def _get_collection(knowledge_dir=None, db_dir=None):
    """
    懒加载 ChromaDB 集合：若 data/chroma_db/ 存在则加载，否则自动建库。
    线程安全：使用锁保证并发建库安全。

    返回：
        chromadb.Collection
    """
    global _collection
    if _collection is not None:
        return _collection

    if knowledge_dir is None:
        knowledge_dir = _DEFAULT_KNOWLEDGE_DIR
    if db_dir is None:
        db_dir = _DEFAULT_DB_DIR

    import chromadb

    with _build_lock:
        if _collection is not None:
            return _collection

        if not _is_index_valid(db_dir):
            print(f"[RAG] 索引不存在，开始构建知识库...")
            _collection = build_index(knowledge_dir, db_dir)
        else:
            print(f"[RAG] 加载已有索引：{db_dir}")
            client = chromadb.PersistentClient(path=db_dir)
            try:
                _collection = client.get_collection(
                    "running_knowledge",
                    embedding_function=_get_embedding_function(),
                )
                # 检测嵌入函数是否变更：若索引是用不同嵌入函数构建的，需重建
                col_meta = _collection.metadata or {}
                stored_fn = col_meta.get("embedding_fn", "")
                if stored_fn != (_embedding_fn_tag or ""):
                    print(f"[RAG] 嵌入函数已变更（{stored_fn or '未知'} → {_embedding_fn_tag}），重建索引...")
                    client.delete_collection("running_knowledge")
                    _collection = build_index(knowledge_dir, db_dir)
            except Exception:
                print("[RAG] 集合读取失败，重建索引...")
                try:
                    client.delete_collection("running_knowledge")
                except Exception:
                    pass
                _collection = build_index(knowledge_dir, db_dir)

    return _collection


def prebuild_index():
    """
    在应用启动时预建索引（非阻塞调用）。
    调用此函数后，首次检索将直接使用缓存索引，无需等待。
    """
    try:
        _get_collection()
        print("[RAG] 索引预加载完成")
    except Exception as e:
        print(f"[RAG] 索引预加载失败（将在首次检索时重试）：{e}")


def build_index(knowledge_dir=None, db_dir=None):
    """
    读取所有知识文档，分块嵌入，持久化到 ChromaDB。

    参数：
        knowledge_dir: 知识文档目录，默认 data/knowledge/
        db_dir:        向量数据库目录，默认 data/chroma_db/

    返回：
        chromadb.Collection
    """
    if knowledge_dir is None:
        knowledge_dir = _DEFAULT_KNOWLEDGE_DIR
    if db_dir is None:
        db_dir = _DEFAULT_DB_DIR

    # 索引内容即将被重建：使检索缓存整体失效
    _invalidate_retrieve_cache()

    import chromadb

    # 扫描知识文档
    if not os.path.isdir(knowledge_dir):
        raise FileNotFoundError(f"[RAG] 知识库目录不存在：{knowledge_dir}")

    md_files = sorted(
        os.path.join(knowledge_dir, f)
        for f in os.listdir(knowledge_dir)
        if f.endswith(".md")
    )
    if not md_files:
        raise FileNotFoundError(
            f"[RAG] 知识库目录为空：{knowledge_dir}\n"
            f"  请确保 data/knowledge/ 下存在至少一个 .md 文件"
        )

    print(f"[RAG] 找到 {len(md_files)} 个知识文档")

    # 分块
    all_chunks = []
    all_metadatas = []
    all_ids = []

    for filepath in md_files:
        filename = os.path.basename(filepath)
        # 治理/运营文档不入库为「知识」
        if _is_governance(filename):
            print(f"[RAG] 跳过治理文件（不入库）：{filename}")
            continue
        chunks = _split_markdown(filepath)
        # 解析文档级元数据（无 meta 块则用默认兜底）
        doc_meta = _parse_meta(filepath) or dict(_DEFAULT_META)
        doc_title = _doc_title(filepath)
        for i, chunk in enumerate(chunks):
            chunk_id = f"{filename}_{i}"
            all_chunks.append(chunk)
            all_metadatas.append({
                "source": filename,
                "title": doc_meta.get("title") or doc_title,
                "chunk_index": i,
                "category": doc_meta.get("category", "自编"),
                "author": doc_meta.get("author", "团队自编"),
                "year": doc_meta.get("year", "未知"),
                "authority_level": doc_meta.get("authority_level", "中"),
                "source_type": doc_meta.get("source_type", "note"),
                "lang": doc_meta.get("lang", "zh"),
                "is_summary": "false",
            })
            all_ids.append(chunk_id)
        # 中文摘要层：为每篇文档增加一份「概述」摘要块，提升中文问句召回
        summ = _extract_summary(filepath)
        if summ:
            all_chunks.append(f"【{doc_title}】中文摘要\n{summ}")
            all_metadatas.append({
                "source": filename,
                "title": doc_meta.get("title") or doc_title,
                "chunk_index": -1,
                "category": doc_meta.get("category", "自编"),
                "author": doc_meta.get("author", "团队自编"),
                "year": doc_meta.get("year", "未知"),
                "authority_level": doc_meta.get("authority_level", "中"),
                "source_type": doc_meta.get("source_type", "note"),
                "lang": doc_meta.get("lang", "zh"),
                "is_summary": "true",
            })
            all_ids.append(f"{filename}_summary")

    print(f"[RAG] 分块完成：{len(all_chunks)} 个文本块")

    # 嵌入 + 持久化
    os.makedirs(db_dir, exist_ok=True)
    client = chromadb.PersistentClient(path=db_dir)

    # 删除旧集合（如果存在）
    try:
        client.delete_collection("running_knowledge")
    except Exception:
        pass

    embedding_fn = _get_embedding_function()
    collection = client.create_collection(
        name="running_knowledge",
        embedding_function=embedding_fn,
        metadata={
            "description": "跑步运动科学知识库",
            "embedding_fn": _embedding_fn_tag or "unknown",
        },
    )

    # 批量添加
    batch_size = 50
    for start in range(0, len(all_chunks), batch_size):
        end = min(start + batch_size, len(all_chunks))
        collection.add(
            documents=all_chunks[start:end],
            metadatas=all_metadatas[start:end],
            ids=all_ids[start:end],
        )
        print(f"[RAG] 嵌入进度：{end}/{len(all_chunks)}")

    print(f"[RAG] 知识库构建完成，索引持久化至：{db_dir}")
    return collection


# ============================================================
# 公共检索接口
# ============================================================

def retrieve_knowledge(query, top_k=3, category=None, authority_boost=0.2):
    """
    检索与查询最相关的运动科学知识片段。

    参数：
        query:            查询文本（中文）
        top_k:            返回最多几条结果（默认 3）
        category:         按类别过滤（如 "公开指南"/"生理基础"），None=不过滤
        authority_boost:  权威度融合权重 0–1，默认 0.2（冲突时高权威优先）

    返回：
        list[dict]，每条包含：
        {
            "content": "知识片段文本（已截断至 800 字）",
            "source":  "文件名.md",
            "title":   "书名/文档标题（来自 meta.title，缺省用 H1）",
            "score":   0.85,           # 原始相似度（0-1）
            "authority_level": "高",  # 文档权威度
            "is_summary": False,       # 是否中文摘要块
            "fused_score": 0.88,      # 融合重排分数
        }

    特殊场景：
        - 空查询 / 仅空白字符 → 返回 []
        - category 过滤后无结果 → 返回 []
        - 数据库未初始化 → 自动建库后检索
    """
    if not query or not query.strip():
        return []

    qkey = query.strip()
    version = _retrieve_cache_version
    with _retrieve_cache_lock:
        hit = _retrieve_cache.get((qkey, top_k, category, authority_boost))
        if hit is not None and hit[0] == version:
            return hit[1]
        cache_key = (qkey, top_k, category, authority_boost)

    col = _get_collection()

    query_args = {
        "query_texts": [query.strip()],
        "n_results": top_k,
    }
    if category:
        query_args["where"] = {"category": category}

    try:
        results = col.query(**query_args)
    except Exception as e:
        print(f"[RAG] 检索失败：{e}")
        return []

    if not results or not results.get("ids") or not results["ids"][0]:
        return []

    output = []
    for i, doc_id in enumerate(results["ids"][0]):
        document = results["documents"][0][i] if results.get("documents") else ""
        metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
        distance = results["distances"][0][i] if results.get("distances") else 0

        # 距离转相似度：Cosin 距离 [0,2] 用 1-d，L2 距离 [0,4] 用 1/(1+d)
        d = float(distance)
        if d <= 2.0:
            # 余弦距离（范围 [0, 2]，其中 0 = 完全相同，2 = 完全相反）
            score = round(max(0.0, 1.0 - d / 2.0), 4)
        else:
            # 平方 L2 距离（范围 [0, 4]，0 = 完全相同）
            score = round(1.0 / (1.0 + d), 4)

        # 截断过长内容（按句子边界）
        content = _truncate_at_sentence_boundary(document, _CONTEXT_MAX_CHARS)

        output.append({
            "content": content,
            "source": metadata.get("source", "unknown.md"),
            "title": metadata.get("title", metadata.get("source", "未知文档")),
            "score": score,
            "authority_level": metadata.get("authority_level", "中"),
            "is_summary": str(metadata.get("is_summary", "false")),
        })

    # 按相似度 + 权威度融合重排（冲突仲裁）
    result = rerank_with_authority(output, boost=authority_boost)

    # 写入缓存（仅当索引版本未变，避免重建竞态写入陈旧结果）；并执行 LRU 淘汰
    with _retrieve_cache_lock:
        if _retrieve_cache_version == version:
            _retrieve_cache[cache_key] = (version, result)
            if cache_key in _retrieve_cache_order:
                _retrieve_cache_order.remove(cache_key)
            _retrieve_cache_order.append(cache_key)
            while len(_retrieve_cache_order) > _retrieve_cache_max:
                oldest = _retrieve_cache_order.pop(0)
                _retrieve_cache.pop(oldest, None)

    return result


def format_knowledge_context(results):
    """
    将检索结果格式化为可注入 LLM 的上下文文本。

    参数：
        results: retrieve_knowledge() 的返回值

    返回：
        str，格式化的知识上下文文本；无结果时返回 ""
    """
    if not results:
        return ""

    lines = ["【运动科学知识库检索结果】"]
    for i, r in enumerate(results, 1):
        auth = r.get("authority_level", "")
        auth_tag = f"，权威度：{auth}" if auth else ""
        lines.append(
            f"\n--- 参考资料 {i}（来源：{r['source']}，相关度：{r['score']}{auth_tag}）---"
        )
        lines.append(r["content"])

    lines.append("")
    lines.append("请基于以上运动科学知识回答用户问题，并在回答末尾标注参考来源。")
    lines.append("【引用格式对照】（请严格逐条抄写以下条目作为「参考来源」，不要改写或编造）：")
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {_format_citation(r)}")
    return "\n".join(lines)


def _format_citation(r):
    """
    生成书名引用串：《书名》（作者，年份）· 文件名.md。

    - 书籍类（book_*.md）：用 meta.title（书名）+ author + year
    - 自编/综述类：无单一作者时用 H1 标题，省略作者年份括号
    """
    title = (r.get("title") or r.get("source") or "未知文档").strip()
    author = (r.get("author") or "").strip()
    year = (r.get("year") or "").strip()
    src = r.get("source") or ""
    has_author = bool(author) and author not in ("团队自编",)
    if has_author and year and year != "未知":
        return f"《{title}》（{author}，{year}）· {src}"
    if has_author:
        return f"《{title}》（{author}）· {src}"
    return f"《{title}》· {src}"


def reset_index():
    """清除内存中的集合引用，下次检索时重新加载。"""
    global _collection
    _collection = None
    # 索引引用已失效，缓存必须同步失效
    _invalidate_retrieve_cache()


# ============================================================
# 模块自测
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("RAG 知识库模块自测")
    print("=" * 50)

    # 测试 1：分块
    print("\n--- 分块测试 ---")
    md_files = sorted(
        os.path.join(_DEFAULT_KNOWLEDGE_DIR, f)
        for f in os.listdir(_DEFAULT_KNOWLEDGE_DIR)
        if f.endswith(".md")
    )
    if md_files:
        chunks = _split_markdown(md_files[0])
        print(f"文件：{os.path.basename(md_files[0])}")
        print(f"分块数：{len(chunks)}")
        if chunks:
            print(f"第一块前 100 字：{chunks[0][:100]}...")
    else:
        print(f"知识库目录为空：{_DEFAULT_KNOWLEDGE_DIR}")

    # 测试 2：索引构建
    print("\n--- 索引构建测试 ---")
    try:
        build_index()
        print("索引构建成功！")
    except Exception as e:
        print(f"索引构建失败：{e}")

    # 测试 3：检索
    print("\n--- 检索测试 ---")
    try:
        results = retrieve_knowledge("间歇跑应该跑多快？", top_k=3)
        print(f"检索到 {len(results)} 条结果：")
        for i, r in enumerate(results):
            print(f"  {i+1}. [{r['source']}] score={r['score']:.3f}")
            print(f"     {r['content'][:80]}...")
    except Exception as e:
        print(f"检索失败：{e}")

    # 测试 4：上下文格式化
    print("\n--- 上下文格式化 ---")
    try:
        ctx = format_knowledge_context(results)
        print(ctx[:500])
    except Exception as e:
        print(f"格式化失败：{e}")