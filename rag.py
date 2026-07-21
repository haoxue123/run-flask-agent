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
# 嵌入函数：优先 sentence-transformers，降级到 ChromaDB 内置
# ============================================================

_embedding_fn = None
_embedding_fn_lock = threading.Lock()


def _get_embedding_function():
    """
    获取嵌入函数，优先使用 sentence-transformers 多语言模型，
    不可用时降级到 ChromaDB 内置的 all-MiniLM-L6-v2。
    线程安全：使用锁保证单例初始化。
    """
    global _embedding_fn
    if _embedding_fn is not None:
        return _embedding_fn

    with _embedding_fn_lock:
        if _embedding_fn is not None:
            return _embedding_fn

        # 尝试加载 sentence-transformers
        try:
            from chromadb.utils import embedding_functions
            _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=_EMBEDDING_MODEL
            )
            print(f"[RAG] 使用嵌入模型：{_EMBEDDING_MODEL}")
            return _embedding_fn
        except Exception as e:
            print(f"[RAG] sentence-transformers 不可用（{e}），降级到 ChromaDB 内置嵌入")

        # 降级：使用 ChromaDB 内置嵌入
        try:
            import chromadb
            _embedding_fn = chromadb.utils.embedding_functions.DefaultEmbeddingFunction()
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
# 索引构建与加载
# ============================================================

_collection = None
_build_lock = threading.Lock()


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
        chunks = _split_markdown(filepath)
        for i, chunk in enumerate(chunks):
            chunk_id = f"{filename}_{i}"
            all_chunks.append(chunk)
            all_metadatas.append({
                "source": filename,
                "chunk_index": i,
            })
            all_ids.append(chunk_id)

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
        metadata={"description": "跑步运动科学知识库"},
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

def retrieve_knowledge(query, top_k=3):
    """
    检索与查询最相关的运动科学知识片段。

    参数：
        query:  查询文本（中文）
        top_k:  返回最多几条结果（默认 3）

    返回：
        list[dict]，每条包含：
        {
            "content": "知识片段文本（已截断至 800 字）",
            "source":  "文件名.md",
            "score":   0.85,  # 相似度分数（0-1），越高越相关
        }

    特殊场景：
        - 空查询 / 仅空白字符 → 返回 []
        - 数据库未初始化 → 自动建库后检索
    """
    if not query or not query.strip():
        return []

    col = _get_collection()

    try:
        results = col.query(
            query_texts=[query.strip()],
            n_results=top_k,
        )
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
            "score": score,
        })

    return output


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
        lines.append(
            f"\n--- 参考资料 {i}（来源：{r['source']}，相关度：{r['score']}）---"
        )
        lines.append(r["content"])

    lines.append("")
    lines.append("请基于以上运动科学知识回答用户问题，并在回答末尾标注参考来源。")
    return "\n".join(lines)


def reset_index():
    """清除内存中的集合引用，下次检索时重新加载。"""
    global _collection
    _collection = None


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