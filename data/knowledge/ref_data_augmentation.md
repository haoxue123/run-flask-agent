# 数据增强与质量过滤（理论文献与技术资料）

> 元数据：`category=data_augmentation` · `authority_level=高` · `source_type=literature_review` · `lang=zh` · `use=KB增强/提示优化/训练数据扩充`
> 中文要点摘要：本文档系统整理文本数据增强的分类（同义替换/回译/EDA/LLM生成/检索增强）与质量过滤方法（困惑度/启发式/去重/模型打分），并落到本项目两类场景——RAG 知识库增强（已验证 Top1 0.50→0.97）与提示词 few-shot 扩充。附权威文献索引，供后续复用。

---

## 一、什么是数据增强（定义）**[纳入·高]**

数据增强（Data Augmentation）指在不改变样本标签/语义的前提下，通过变换生成新的训练/检索样本，以扩充数据规模、提升模型泛化与鲁棒性。在 LLM/RAG 工程里，"训练数据"可指：提示词与 few-shot 示例、检索知识库、评测数据集。增强目标是**用更丰富、更覆盖长尾的数据提升下游指标**（如本项目的 RAG 检索 Top1）。

---

## 二、文本数据增强分类 **[纳入·高]**

### 2.1 词/字级变换（EDA 家族）
- **同义替换（Synonym Replacement）**：用同义词替换非停用词（如"恢复"→"放松/超量恢复"）。本项目即用此法为知识库加「检索增强层」。**直接提升中文查询召回**。
- **随机插入 / 删除 / 调换（Random Insertion/Deletion/Swap）**：EDA（Wei & Zou, 2019）四操作，对小样本文本分类增益显著（少量样本 +4% 上下）。
- **字符级扰动**：针对拼写/形态鲁棒性，对中文意义有限。

### 2.2 回译（Back-Translation）**[纳入·高]**
- 将样本译到中间语言再译回，得到语义一致、表述多样的复述（Sennrich et al., 2016）。适合生成问法变体（本项目"常见问法"扩展的同质思路）。

### 2.3 检索增强（Retrieval-Augmentation / RAG 本身）**[纳入·高]**
- 向模型注入外部知识片段即是"用检索到的数据增强上下文"（Lewis et al., 2020, RAG）。本项目 `rag.py` 即此范式；**知识库的规模与质量直接决定增强上限**——这正是本次实验的落脚点。

### 2.4 大模型生成式增强（LLM-based Augmentation）**[纳入·中]**
- 用强模型基于原样本生成新样本/释义/对抗样本（Kumar et al., 2020）。质量高但需**质量过滤**控成本与幻觉（见第三节）。

### 2.5 其它
- **Mixup / 嵌入插值**：连续表示插值，多用于分类特征，文本适用性弱。
- **子序列/裁剪**：保留核心片段，适合长文档摘要增强。

---

## 三、质量过滤方法（增强的必备闸门）**[纳入·高]**

> 本项目实证：naive 增强（只加文档未过滤）使 RAG Top1 从 0.50 跌到 0.30；加质量过滤后升至 0.97。过滤不是走过场。

### 3.1 去重（Deduplication）
- 精确去重（hash）+ 近似去重（嵌入相似度 / MinHash）（Lee et al., 2022：去重可提升 LLM 困惑度与下游表现）。本项目 `filter_kb.py` 检测同文档近似重复分块。

### 3.2 困惑度 / 启发式过滤
- 用语言模型困惑度剔除低质/乱码文本（Wenzek et al., 2020, CCNet）；启发式规则（长度、格式、语言、符号比）快速筛除噪声。

### 3.3 模型打分过滤
- 训练质量分类器或让 LLM 裁判打分（Penedo et al., 2024, FineWeb：用分类器滤除低质网页）。适用于 LLM 生成式增强的产物。

### 3.4 冲突与一致性核查
- 对量化事实（配速、跑量、距离）做一致性扫描，多源冲突时按权威度加权、呈现多源（本项目 `知识库规划 §3` 冲突治理）。

### 3.5 范围限定（本项目关键经验）
- 新增知识（如日本/中国方法）的增强层应**限定主题范围**，避免其通用词外溢抢走通用查询的检索首位——这是本项目"质量过滤"的独特一环。

---

## 四、在本项目的落地映射 **[纳入·高]**

| 场景 | 增强技术 | 质量过滤 | 实测收益 |
|------|----------|----------|----------|
| RAG 知识库 | 同义替换 + 问法扩展（检索增强层） | 范围限定 + 去重 + 回归门禁 | Top1 0.50→0.97 |
| 提示词 few-shot | 回译/LLM 生成问法变体 | 模型打分 + 去重 | （待做） |
| 评测数据集 | 合成合理变体扩覆盖 | 格式/范围校验 | （待做） |

---

## 五、权威文献索引（开放可查）

- Wei J., Zou K. (2019). *EDA: Easy Data Augmentation Techniques for Boosting Performance on Text Classification Tasks*. EMNLP.
- Feng S. et al. (2021). *A Survey of Data Augmentation Approaches for Natural Language Processing*. ACL.
- Sennrich R. et al. (2016). *Improving Neural Machine Translation with Back-Translation*. ACL.
- Kumar V. et al. (2020). *Data Augmentation using Pre-trained Transformer Models*. LREC.
- Lewis P. et al. (2020). *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*. NeurIPS.
- Lee K. et al. (2022). *Deduplicating Training Data Makes Language Models Better*. ACL.
- Wenzek G. et al. (2020). *CCNet: Extracting High Quality Monolingual Datasets from Web Crawl*. LREC.
- Penedo G. et al. (2024). *The FineWeb Datasets: Decanting the Web for the Finest Text Data at Scale*.

**[合规]** 以上为公开学术论文综述索引（开放获取/可引用），仅做方法索引与改写概述，未复制正文；符合 `知识库规划 §2.1` 合规 gate。

> 关联知识库：`知识库专业书籍引入规划.md`（筛选标准与合规）、`method_japan_china.md`（本实验增强对象）、`data/eval/augmentation_regression_report.md`（实测结论）。
