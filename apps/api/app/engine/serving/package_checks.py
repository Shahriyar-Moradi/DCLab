"""Consistency checks for a model package's members (P7.1-A security review). WORKER SIDE.

skops refuses code it does not trust, but the scikit-learn and booster C/C++ predict
paths trust the arrays they are given: a crafted tree node index or a lying LightGBM
header can read out of bounds or abort the process. These checks run on the loaded
(or, for native text/JSON, the not-yet-parsed) members and refuse anything that is not
exactly the shape the registry trains, before any row is predicted:

* the estimator is the class ``make_model(family)`` builds (Pipeline steps included);
* ``prep`` is the ``build_preprocessor`` ColumnTransformer with a consistent encoder;
* every scikit-learn tree: children in range and after their parent (so traversal
  ends), split features inside the input width, value table sized to the nodes;
* gradient boosting: tree grid and initial-prediction shape match the classes;
* XGBoost JSON and LightGBM text are never handed to the library as stored: a strict parser
  (duplicate keys refused, so an escaped duplicate cannot hide; plain-digit integers only; a
  whitelist of every key and value) validates them and the model is REBUILT from the
  validated values (``canonical_xgboost_json`` / ``canonical_lightgbm_text``).

``package_loader`` runs the rebuild itself in a memory-limited child process (the worker never
parses stored native bytes), then loads the rebuilt bytes (CatBoost: the stored ``cbm``, whose
JSON view ``check_catboost_json`` validates inside the child) and predicts probe rows in a
short-lived child; a crash, hang or refused limit there refuses the model. The child isolates
crashes and memory, not secrets: it shares the worker's uid, filesystem and network.

Ops note: the whitelists are exact, so a package written by a future XGBoost/LightGBM whose
format adds or renames a key is refused (fail closed) until the whitelist is reviewed.
"""

from __future__ import annotations

import json
import re
from typing import Any

import numpy as np

from app.engine.serving.model_package import ModelPackageError

TREE_LEAF = -1
MAX_TREES = 200_000  # e.g. 2,000 boosting rounds x up to 100 classes
MAX_TREE_NODES = 50_000_000
LGBM_MAX_LEAVES = 131_072


class PackageCheckError(ModelPackageError):
    """A member is not the consistent shape the registry trains (refused before predicting)."""


def _fail(message: str) -> None:
    raise PackageCheckError(message)


def estimator_signature(model: Any) -> Any:
    """Class names that identify the trained estimator (Pipeline steps / wrapper included)."""

    steps = getattr(model, "steps", None)
    if isinstance(steps, list):
        return ("Pipeline", tuple((name, type(step).__name__) for name, step in steps))
    inner = getattr(model, "estimator", None)
    if type(model).__name__ == "ContiguousLabelClassifier":
        inner = getattr(model, "estimator_", inner)
        return ("ContiguousLabelClassifier", type(inner).__name__)
    return type(model).__name__


def expected_signature(family: str, task_type: str, model_facts: dict[str, Any]) -> Any:
    from app.engine.models.registry import make_model

    try:
        signature = estimator_signature(make_model(family, task_type=task_type))
    except Exception:  # noqa: BLE001 - unknown family or a library that is not installed
        _fail("model family is not one the registry trains")
    if model_facts.get("estimator_format") == "lightgbm_text":
        return "LightGBMBoosterModel"  # the native booster is wrapped, never the sklearn class
    return signature


# --- preprocessing --------------------------------------------------------------------


def check_preprocessor(prep: Any, features: list[str]) -> int:
    """The ``build_preprocessor`` ColumnTransformer; returns its output width."""

    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    if type(prep) is not ColumnTransformer or list(getattr(prep, "feature_names_in_", [])) != features:
        _fail("preprocessor is not the expected ColumnTransformer")
    expected = {"num": (SimpleImputer, StandardScaler), "cat": (SimpleImputer, OneHotEncoder)}
    width = 0
    for name, transformer, columns in prep.transformers_:
        if name == "remainder":
            if transformer != "drop":
                _fail("preprocessor keeps unlisted columns")
            continue
        steps = getattr(transformer, "steps", None)
        if name not in expected or type(transformer) is not Pipeline or [type(s) for _n, s in steps] != list(
            expected[name]
        ):
            _fail("preprocessor step is not the expected pipeline")
        if name == "num":
            width += len(columns)
            continue
        encoder = steps[1][1]
        categories = getattr(encoder, "categories_", None)
        if (
            not isinstance(categories, list)
            or len(categories) != len(columns)
            or encoder.sparse_output is not False
            or encoder.handle_unknown != "ignore"
            or getattr(encoder, "drop_idx_", None) is not None
            or getattr(encoder, "_infrequent_enabled", False)
            or any(not isinstance(c, np.ndarray) or c.ndim != 1 for c in categories)
            or list(getattr(encoder, "_n_features_outs", [len(c) for c in categories])) != [len(c) for c in categories]
        ):
            _fail("one-hot encoder state is inconsistent")
        width += sum(len(c) for c in categories)
    return width


# --- scikit-learn trees ------------------------------------------------------------------


def check_sklearn_tree(estimator: Any, width: int, *, n_values: int | None = None) -> None:
    tree = getattr(estimator, "tree_", None)
    if type(tree).__name__ != "Tree" or type(tree).__module__ != "sklearn.tree._tree":
        _fail("tree estimator without a tree")
    state = tree.__getstate__()
    nodes, values, count = state.get("nodes"), state.get("values"), state.get("node_count")
    if not isinstance(nodes, np.ndarray) or not isinstance(count, (int, np.integer)):
        _fail("tree state is not an array")
    if not 1 <= count <= MAX_TREE_NODES or len(nodes) != count or tree.n_features != width or tree.n_outputs != 1:
        _fail("tree size or input width is inconsistent")
    left, right = nodes["left_child"].astype(np.int64), nodes["right_child"].astype(np.int64)
    feature, index = nodes["feature"].astype(np.int64), np.arange(count)
    leaf = left == TREE_LEAF
    internal = ~leaf
    if np.any(right[leaf] != TREE_LEAF):
        _fail("tree leaf has a child")
    # Children come after their parent (how scikit-learn builds trees): in range and acyclic.
    for child in (left[internal], right[internal]):
        if np.any(child <= index[internal]) or np.any(child >= count):
            _fail("tree child index out of range")
    if np.any(feature[internal] < 0) or np.any(feature[internal] >= width):
        _fail("tree split feature out of range")
    max_classes = int(tree.max_n_classes)
    if not isinstance(values, np.ndarray) or values.shape != (count, 1, max_classes):
        _fail("tree value table does not match its nodes")
    if n_values is not None and max_classes != n_values:
        _fail("tree value width does not match the model")


def check_sklearn_estimator(model: Any, width: int) -> None:
    """Trees inside forests and gradient boosting (the registry's tree families)."""

    name = type(model).__name__
    if name in {"RandomForestClassifier", "RandomForestRegressor", "ExtraTreesClassifier", "ExtraTreesRegressor"}:
        expected_tree = name.replace("RandomForest", "DecisionTree").replace("ExtraTrees", "ExtraTree")
        trees = getattr(model, "estimators_", None)
        if not isinstance(trees, list) or not 1 <= len(trees) <= MAX_TREES or model.n_features_in_ != width:
            _fail("forest is inconsistent")
        for tree in trees:
            if type(tree).__name__ != expected_tree:
                _fail("forest member is not the expected tree")
            check_sklearn_tree(tree, width, n_values=len(model.classes_) if hasattr(model, "classes_") else 1)
    elif name in {"GradientBoostingClassifier", "GradientBoostingRegressor"}:
        grid = getattr(model, "estimators_", None)
        classes = len(getattr(model, "classes_", [])) if name.endswith("Classifier") else 0
        per_iteration = classes if classes > 2 else 1
        if (
            not isinstance(grid, np.ndarray)
            or grid.ndim != 2
            or grid.shape[1] != per_iteration
            or not 1 <= grid.shape[0] <= MAX_TREES
            or model.n_trees_per_iteration_ != per_iteration
            or model.n_features_in_ != width
            or type(model.init_).__name__ not in {"DummyClassifier", "DummyRegressor"}
        ):
            _fail("gradient boosting grid is inconsistent")
        for tree in grid.ravel():
            if type(tree).__name__ != "DecisionTreeRegressor":
                _fail("boosting stage is not a regression tree")
            check_sklearn_tree(tree, width, n_values=1)
        # The Cython stage loop writes into this array: it must be (rows, trees per stage).
        if model._raw_predict_init(np.zeros((2, width))).shape != (2, per_iteration):
            _fail("gradient boosting initial prediction has the wrong shape")


# --- native boosters: parse strictly, validate, re-serialize (the library parses OUR bytes) --
#
# The library never sees the stored bytes: a strict parser (no duplicate keys, no escapes the
# library would read differently, integers as plain digits only, a whitelist of every key) reads
# them, every value is validated, and the model is rebuilt from the validated values. The
# rebuilt bytes are what the sandboxed probe and the worker load.

_INT = re.compile(r"-?\d+", re.ASCII)
_FLOAT = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", re.ASCII)
_ROOT_PARENT = 2147483647  # XGBoost's "no parent"


def _keys(obj: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != expected:
        _fail(f"{label}: keys differ from the format")
    return obj


def _int_text(value: Any, low: int, high: int, label: str) -> int:
    if not isinstance(value, str) or not _INT.fullmatch(value) or not low <= int(value) <= high:
        _fail(f"{label}: not an integer in range")
    return int(value)


def _float_text(value: Any, label: str) -> float:
    if not isinstance(value, str) or not _FLOAT.fullmatch(value) or not np.isfinite(float(value)):
        _fail(f"{label}: not a finite number")
    return float(value)


def _int_list(values: Any, size: int, low: int, high: int, label: str) -> list[int]:
    if not isinstance(values, list) or len(values) != size or any(type(v) is not int for v in values):
        _fail(f"{label}: not {size} integers")
    if values and (min(values) < low or max(values) > high):
        _fail(f"{label}: out of range")
    return values


def _float_list(values: Any, size: int, label: str) -> list[float]:
    if not isinstance(values, list) or len(values) != size or any(type(v) not in (int, float) for v in values):
        _fail(f"{label}: not {size} numbers")
    out = [float(v) for v in values]
    if out and not np.all(np.isfinite(out)):
        _fail(f"{label}: not finite")
    return out


def _check_tree_shape(left: np.ndarray, right: np.ndarray, size: int, label: str) -> np.ndarray:
    """Children in range, never the root, each node referenced at most once -> a finite tree.
    Returns the internal node indices (leaves have -1 children)."""

    leaf = left == TREE_LEAF
    if np.any(right[leaf] != TREE_LEAF) or np.any(right[~leaf] == TREE_LEAF):
        _fail(f"{label}: leaf/child mismatch")
    children = np.concatenate([left[~leaf], right[~leaf]])
    if np.any(children <= 0) or np.any(children >= size) or len(np.unique(children)) != len(children):
        _fail(f"{label}: child index out of range or shared")
    return np.flatnonzero(~leaf)


def _strict_json(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                _fail("duplicate key")  # also catches an escaped spelling of the same key
            out[key] = value
        return out

    def constant(_name: str) -> None:
        _fail("non-finite number")

    try:
        return json.loads(data.decode("ascii"), object_pairs_hook=pairs, parse_constant=constant)
    except PackageCheckError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise PackageCheckError("XGBoost member is not strict JSON") from exc


_XGB_TREE_KEYS = {
    "base_weights", "categories", "categories_nodes", "categories_segments", "categories_sizes", "default_left",
    "id", "left_children", "loss_changes", "parents", "right_children", "split_conditions", "split_indices",
    "split_type", "sum_hessian", "tree_param",
}


def _xgb_tree(tree: Any, index: int, width: int) -> dict[str, Any]:
    label = f"XGBoost tree {index}"
    _keys(tree, _XGB_TREE_KEYS, label)
    param = _keys(tree["tree_param"], {"num_deleted", "num_feature", "num_nodes", "size_leaf_vector"}, label)
    size = _int_text(param["num_nodes"], 1, MAX_TREE_NODES, label)
    _int_text(param["num_deleted"], 0, 0, label)
    _int_text(param["num_feature"], width, width, label)
    _int_text(param["size_leaf_vector"], 1, 1, label)
    if type(tree["id"]) is not int or tree["id"] != index:
        _fail(f"{label}: id")
    if any(tree[key] != [] for key in ("categories", "categories_nodes", "categories_segments", "categories_sizes")):
        _fail(f"{label}: categorical splits are not produced by DCLab")
    left = _int_list(tree["left_children"], size, -1, size - 1, label)
    right = _int_list(tree["right_children"], size, -1, size - 1, label)
    parents = _int_list(tree["parents"], size, 0, _ROOT_PARENT, label)
    split = _int_list(tree["split_indices"], size, 0, width - 1, label)
    default_left = _int_list(tree["default_left"], size, 0, 1, label)
    split_type = _int_list(tree["split_type"], size, 0, 0, label)
    left_a, right_a, parents_a = np.asarray(left, np.int64), np.asarray(right, np.int64), np.asarray(parents, np.int64)
    internal = _check_tree_shape(left_a, right_a, size, label)
    if parents_a[0] != _ROOT_PARENT or np.any(parents_a[left_a[internal]] != internal) or np.any(
        parents_a[right_a[internal]] != internal
    ):
        _fail(f"{label}: parents do not match children")
    return {
        "base_weights": _float_list(tree["base_weights"], size, label),
        "categories": [], "categories_nodes": [], "categories_segments": [], "categories_sizes": [],
        "default_left": default_left, "id": index, "left_children": left,
        "loss_changes": _float_list(tree["loss_changes"], size, label), "parents": parents, "right_children": right,
        "split_conditions": _float_list(tree["split_conditions"], size, label), "split_indices": split,
        "split_type": split_type, "sum_hessian": _float_list(tree["sum_hessian"], size, label),
        "tree_param": {"num_deleted": "0", "num_feature": str(width), "num_nodes": str(size), "size_leaf_vector": "1"},
    }


def canonical_xgboost_json(data: bytes, width: int, n_classes: int, task_type: str) -> bytes:
    """``n_classes``: 0 for binary/regression (XGBoost's convention), else the class count.
    Returns the rebuilt JSON model; anything outside the whitelisted format is refused."""

    model = _keys(_strict_json(data), {"learner", "version"}, "XGBoost model")
    version = _int_list(model["version"], 3, 0, 1000, "XGBoost version")
    learner = _keys(model["learner"], {"attributes", "feature_names", "feature_types", "gradient_booster",
                                       "learner_model_param", "objective"}, "XGBoost learner")
    kind = "regressor" if task_type == "regression" else "classifier"
    attributes = _keys(learner["attributes"], {"scikit_learn"}, "XGBoost attributes")
    if not isinstance(attributes["scikit_learn"], str) or _strict_json(
        attributes["scikit_learn"].encode("ascii", "replace")
    ) != {"_estimator_type": kind} or learner["feature_names"] != [] or learner["feature_types"] != []:
        _fail("XGBoost attributes differ from the format")
    per_iteration = max(n_classes, 1)
    param = _keys(learner["learner_model_param"], {"base_score", "boost_from_average", "num_class", "num_feature",
                                                   "num_target"}, "XGBoost parameters")
    base = param["base_score"]
    if not isinstance(base, str) or not base.startswith("[") or not base.endswith("]"):
        _fail("XGBoost base score")
    base_score = [_float_text(item, "XGBoost base score") for item in base[1:-1].split(",")]
    if len(base_score) != per_iteration:
        _fail("XGBoost base score size")
    boost = _int_text(param["boost_from_average"], 0, 1, "XGBoost boost_from_average")
    _int_text(param["num_class"], n_classes, n_classes, "XGBoost num_class")
    _int_text(param["num_feature"], width, width, "XGBoost num_feature")
    _int_text(param["num_target"], 1, 1, "XGBoost num_target")
    if task_type == "multiclass":
        objective = {"name": "multi:softprob", "softmax_multiclass_param": {"num_class": str(n_classes)}}
        if learner["objective"] != objective:
            _fail("XGBoost objective")
    else:
        stored = _keys(learner["objective"], {"name", "reg_loss_param"}, "XGBoost objective")
        name = "binary:logistic" if task_type == "binary" else "reg:squarederror"
        weight = _float_text(_keys(stored["reg_loss_param"], {"scale_pos_weight"}, "XGBoost objective")[
            "scale_pos_weight"], "XGBoost scale_pos_weight")
        if stored["name"] != name or weight <= 0:
            _fail("XGBoost objective")
        objective = {"name": name, "reg_loss_param": {"scale_pos_weight": repr(weight)}}
    booster = _keys(learner["gradient_booster"], {"model", "name"}, "XGBoost booster")
    trees_model = _keys(booster["model"], {"cats", "gbtree_model_param", "iteration_indptr", "tree_info", "trees"},
                        "XGBoost gbtree")
    gbtree_param = _keys(trees_model["gbtree_model_param"], {"num_parallel_tree", "num_trees"}, "XGBoost gbtree")
    count = _int_text(gbtree_param["num_trees"], 1, MAX_TREES, "XGBoost num_trees")
    _int_text(gbtree_param["num_parallel_tree"], 1, 1, "XGBoost num_parallel_tree")
    if (
        booster["name"] != "gbtree"
        or trees_model["cats"] != {"enc": [], "feature_segments": [], "sorted_idx": []}
        or not isinstance(trees_model["trees"], list) or len(trees_model["trees"]) != count or count % per_iteration
        or trees_model["tree_info"] != [index % per_iteration for index in range(count)]
        or trees_model["iteration_indptr"] != list(range(0, count + 1, per_iteration))
    ):
        _fail("XGBoost tree layout differs from the format")
    canonical = {
        "learner": {
            "attributes": {"scikit_learn": json.dumps({"_estimator_type": kind})},
            "feature_names": [], "feature_types": [],
            "gradient_booster": {"model": {
                "cats": {"enc": [], "feature_segments": [], "sorted_idx": []},
                "gbtree_model_param": {"num_parallel_tree": "1", "num_trees": str(count)},
                "iteration_indptr": list(range(0, count + 1, per_iteration)),
                "tree_info": [index % per_iteration for index in range(count)],
                "trees": [_xgb_tree(tree, index, width) for index, tree in enumerate(trees_model["trees"])],
            }, "name": "gbtree"},
            "learner_model_param": {
                "base_score": "[" + ",".join(repr(value) for value in base_score) + "]",
                "boost_from_average": str(boost), "num_class": str(n_classes), "num_feature": str(width),
                "num_target": "1",
            },
            "objective": objective,
        },
        "version": version,
    }
    return json.dumps(canonical, separators=(",", ":"), allow_nan=False).encode("ascii")


_LGBM_HEADER = ("version", "num_class", "num_tree_per_iteration", "label_index", "max_feature_idx", "objective",
                "feature_names", "feature_infos", "tree_sizes")
_LGBM_TREE = ("num_leaves", "num_cat", "split_feature", "split_gain", "threshold", "decision_type", "left_child",
              "right_child", "leaf_value", "leaf_weight", "leaf_count", "internal_value", "internal_weight",
              "internal_count", "is_linear", "shrinkage")
_LGBM_INTS = {"split_feature", "decision_type", "left_child", "right_child", "leaf_count", "internal_count"}


def _lgbm_fields(lines: list[str], expected: tuple[str, ...], label: str) -> dict[str, str]:
    """Exactly these keys, in this order (so no duplicate, unknown or reordered key)."""

    if len(lines) != len(expected):
        _fail(f"{label}: unexpected lines")
    fields = {}
    for line, key in zip(lines, expected):
        name, sep, value = line.partition("=")
        if not sep or name != key:
            _fail(f"{label}: unexpected key")
        fields[name] = value
    return fields


def _lgbm_values(value: str, size: int | None, integer: bool, label: str) -> list[Any]:
    tokens = value.split(" ") if value else []
    pattern = _INT if integer else _FLOAT
    if (size is not None and len(tokens) != size) or not all(pattern.fullmatch(token) for token in tokens):
        _fail(f"{label}: malformed values")
    values = [int(token) if integer else float(token) for token in tokens]
    if not integer and values and not np.all(np.isfinite(values)):
        _fail(f"{label}: not finite")
    return values


def _lgbm_tree(block: str, index: int, width: int) -> str:
    label = f"LightGBM tree {index}"
    lines = block.split("\n")
    if lines[0] != f"Tree={index}":
        _fail(f"{label}: misaligned")
    fields = _lgbm_fields(lines[1:], _LGBM_TREE, label)
    leaves = _lgbm_values(fields["num_leaves"], 1, True, label)[0]
    if not 1 <= leaves <= LGBM_MAX_LEAVES or fields["num_cat"] != "0" or fields["is_linear"] != "0":
        _fail(f"{label}: size, categorical or linear section out of range")
    splits = leaves - 1
    sizes = {"split_feature": splits, "split_gain": splits, "threshold": splits, "decision_type": splits,
             "left_child": splits, "right_child": splits, "leaf_value": leaves, "leaf_count": leaves,
             "internal_value": splits, "internal_weight": splits, "internal_count": splits, "shrinkage": 1,
             "leaf_weight": leaves if leaves > 1 or fields["leaf_weight"] else 0}
    parsed = {key: _lgbm_values(fields[key], size, key in _LGBM_INTS, label) for key, size in sizes.items()}
    feature, decision = np.asarray(parsed["split_feature"], np.int64), np.asarray(parsed["decision_type"], np.int64)
    if np.any(feature < 0) or np.any(feature >= width) or np.any(decision & 1) or np.any(((decision >> 2) & 3) > 2):
        _fail(f"{label}: split feature or decision type out of range")
    children = np.asarray(parsed["left_child"] + parsed["right_child"], np.int64)
    # Internal children 1..splits-1 and leaves (~child) 0..leaves-1, each referenced exactly once
    # (a one-leaf tree, written for tiny training pools, has no children at all).
    if splits and (sorted(children[children >= 0].tolist()) != list(range(1, splits)) or sorted(
        (~children[children < 0]).tolist()
    ) != list(range(leaves))):
        _fail(f"{label}: nodes do not form a tree")
    out = [f"Tree={index}", f"num_leaves={leaves}", "num_cat=0"]
    for key in _LGBM_TREE[2:-2]:
        out.append(f"{key}=" + " ".join(str(v) if key in _LGBM_INTS else repr(v) for v in parsed[key]))
    out += ["is_linear=0", f"shrinkage={parsed['shrinkage'][0]!r}"]
    return "\n".join(out) + "\n\n\n"


def canonical_lightgbm_text(data: bytes, width: int, n_classes: int, task_type: str) -> bytes:
    """``n_classes``: 1 for binary/regression (LightGBM's convention), else the class count.
    Returns the rebuilt model text (header + trees; the stored tail sections are dropped)."""

    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise PackageCheckError("LightGBM member is not text") from exc
    head, _sep, rest = text.partition("\n\n")
    body, found, _tail = rest.partition("end of trees")
    lines = head.split("\n")
    if "\r" in text or not found or lines[0] != "tree":
        _fail("LightGBM text is truncated or not a model")
    header = _lgbm_fields(lines[1:], _LGBM_HEADER, "LightGBM header")
    objective = {"binary": "binary sigmoid:1", "multiclass": f"multiclass num_class:{n_classes}",
                 "regression": "regression"}.get(task_type)
    infos = header["feature_infos"].split(" ")
    if (
        header["version"] != "v4" or header["label_index"] != "0" or header["objective"] != objective
        or _lgbm_values(header["num_class"], 1, True, "LightGBM num_class") != [n_classes]
        or _lgbm_values(header["num_tree_per_iteration"], 1, True, "LightGBM trees per iteration") != [n_classes]
        or _lgbm_values(header["max_feature_idx"], 1, True, "LightGBM width") != [width - 1]
        or header["feature_names"] != " ".join(f"Column_{i}" for i in range(width)) or len(infos) != width
    ):
        _fail("LightGBM header does not match the manifest")
    canonical_infos = []
    for info in infos:
        low, sep, high = info[1:-1].partition(":")
        if info == "none":
            canonical_infos.append(info)
        elif info.startswith("[") and info.endswith("]") and sep:
            canonical_infos.append(f"[{_float_text(low, 'LightGBM info')!r}:{_float_text(high, 'LightGBM info')!r}]")
        else:
            _fail("LightGBM feature info malformed")
    blocks = body.split("\n\n\n")
    if not body or blocks[-1] != "" or not 1 <= len(blocks) - 1 <= MAX_TREES or (len(blocks) - 1) % n_classes:
        _fail("LightGBM tree section malformed")
    trees = [_lgbm_tree(block, index, width) for index, block in enumerate(blocks[:-1])]
    if len(_lgbm_values(header["tree_sizes"], None, True, "LightGBM tree sizes")) != len(trees):
        _fail("LightGBM tree sizes do not match the trees")
    rebuilt = [
        "tree", "version=v4", f"num_class={n_classes}", f"num_tree_per_iteration={n_classes}", "label_index=0",
        f"max_feature_idx={width - 1}", f"objective={objective}",
        "feature_names=" + " ".join(f"Column_{i}" for i in range(width)),
        "feature_infos=" + " ".join(canonical_infos), "tree_sizes=" + " ".join(str(len(tree)) for tree in trees),
    ]
    return ("\n".join(rebuilt) + "\n\n" + "".join(trees) + "end of trees\n\npandas_categorical:null\n").encode("ascii")


def check_catboost_json(text: str, width: int, dimension: int) -> int:
    """CatBoost's own JSON view of a ``cbm`` it parsed in a probe process: float splits only,
    feature and border references in range, leaf tables sized 2**depth (x ``dimension``).
    Returns the tree count."""

    try:
        model = json.loads(text)
        floats = model["features_info"]["float_features"]
        trees = model["oblivious_trees"]
        indices = [int(f["feature_index"]) for f in floats]
        flat = [int(f["flat_feature_index"]) for f in floats]
        borders = {index: [float(b) for b in f.get("borders") or []] for index, f in zip(indices, floats)}
        extra = set(model["features_info"]) - {"float_features"}
    except (ValueError, KeyError, TypeError) as exc:
        raise PackageCheckError("CatBoost model view is malformed") from exc
    if extra or not indices or indices != flat or indices != sorted(set(indices)) or indices[-1] >= width or indices[0] < 0:
        _fail("CatBoost features do not match the input width")
    # A split's global border index: the borders of the features before it, plus its position.
    offsets = dict(zip(indices, np.cumsum([0] + [len(borders[i]) for i in indices[:-1]]).tolist()))
    if not isinstance(trees, list) or not 1 <= len(trees) <= MAX_TREES:
        _fail("CatBoost tree count out of range")
    for tree in trees:
        try:
            splits = tree["splits"]
            depth = len(splits)
            if depth > 16 or len(tree["leaf_values"]) != (2**depth) * dimension or len(tree["leaf_weights"]) != 2**depth:
                _fail("CatBoost leaf table does not match the tree depth")
            for split in splits:
                index = int(split["float_feature_index"])
                values = borders.get(index, [])
                border = float(split["border"])
                if (
                    split.get("split_type") != "FloatFeature" or border not in values
                    or int(split["split_index"]) != offsets[index] + values.index(border)
                ):
                    _fail("CatBoost split out of range")
        except (KeyError, TypeError, ValueError) as exc:
            raise PackageCheckError("CatBoost tree is malformed") from exc
    return len(trees)


def check_catboost(estimator: Any, width: int, tree_count: int) -> None:
    # A loaded cbm model does not restore n_features_in_; its feature names are the inputs.
    if len(estimator.feature_names_ or []) != width or int(estimator.tree_count_) != tree_count:
        _fail("CatBoost model does not match the input width")
