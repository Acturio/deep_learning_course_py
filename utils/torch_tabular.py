"""Herramientas para clasificación tabular con Spark, PyArrow y PyTorch DDP.

El módulo no codifica variables identificadoras: las columnas de entrada se
determinan explícitamente durante la preparación de los datos.
"""

from __future__ import annotations

import hashlib
import io
import os
import random
from contextlib import nullcontext
from typing import Any, Iterable, Sequence

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.fs as pafs
import torch
import torch.distributed as dist
import torch.nn as nn
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.utils.data import DataLoader, IterableDataset, get_worker_info


def configure_ssl() -> None:
    """Propaga el certificado corporativo a librerías usadas por los workers."""
    bundle = os.environ.get(
        "CA_BUNDLE", "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"
    )
    for variable in (
        "SSL_CERT_FILE",
        "AWS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "REQUESTS_CA_BUNDLE",
        "ARROW_SSL_CERT_FILE",
    ):
        os.environ[variable] = bundle


def set_seed(seed: int) -> None:
    """Fija las semillas usadas por Python, NumPy y PyTorch."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def numeric_feature_columns(schema: Any, target_col: str, id_col: str) -> list[str]:
    """Devuelve campos numéricos del esquema Spark, excluyendo target e ID.

    No intenta codificar columnas de texto. Esto evita incluir accidentalmente
    identificadores de alta cardinalidad, entre ellos ``customer_id``.
    """
    from pyspark.sql import types as T

    numeric_types = (
        T.ByteType,
        T.ShortType,
        T.IntegerType,
        T.LongType,
        T.FloatType,
        T.DoubleType,
        T.DecimalType,
        T.BooleanType,
    )
    excluded = {target_col, id_col}
    features = [
        field.name
        for field in schema.fields
        if field.name not in excluded and isinstance(field.dataType, numeric_types)
    ]
    if not features:
        raise ValueError("No se detectaron columnas numéricas predictoras.")
    return features


def get_or_create_spark(existing_spark: Any | None = None) -> Any:
    """Reutiliza Spark de Livy cuando se proporciona o crea una sesión local."""
    if existing_spark is not None:
        return existing_spark
    from pyspark.sql import SparkSession

    return SparkSession.builder.appName("torch-tabular-preparation").getOrCreate()


def infer_numeric_features(
    train_path: str,
    target_col: str,
    id_col: str,
    spark: Any | None = None,
) -> list[str]:
    """Infiera columnas de entrada desde train; ``id_col`` queda excluida."""
    spark = get_or_create_spark(spark)
    schema = spark.read.parquet(train_path).schema
    required = {target_col, id_col}
    missing = required.difference(schema.fieldNames())
    if missing:
        raise ValueError(f"Faltan columnas requeridas en train: {sorted(missing)}")
    return numeric_feature_columns(schema, target_col=target_col, id_col=id_col)


def compute_standardization_stats(
    train_path: str,
    feature_cols: Sequence[str],
    spark: Any | None = None,
    fill_value: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Calcula media y desviación poblacional exclusivamente con train."""
    from pyspark.sql import functions as F

    spark = get_or_create_spark(spark)
    frame = spark.read.parquet(train_path).select(*feature_cols)
    for column in feature_cols:
        frame = frame.withColumn(column, F.col(column).cast("double"))
    frame = frame.fillna(float(fill_value), subset=list(feature_cols))

    aggregations = []
    for column in feature_cols:
        aggregations.extend(
            [
                F.avg(column).alias(f"{column}__mean"),
                F.stddev_pop(column).alias(f"{column}__std"),
            ]
        )
    values = frame.agg(*aggregations).first().asDict()
    means = np.asarray(
        [float(values.get(f"{column}__mean") or 0.0) for column in feature_cols],
        dtype=np.float32,
    )
    stds = np.asarray(
        [
            max(float(values.get(f"{column}__std") or 0.0), 1e-8)
            for column in feature_cols
        ],
        dtype=np.float32,
    )
    stds[stds < 1e-8] = 1.0
    return means, stds


def prepare_tabular_split(
    input_path: str,
    output_path: str,
    feature_cols: Sequence[str],
    target_col: str,
    means: np.ndarray,
    stds: np.ndarray,
    spark: Any | None = None,
    repartitions: int = 256,
    write_mode: str = "overwrite",
    fill_value: float = 0.0,
) -> str:
    """Estandariza una partición y escribe solo features y target.

    La salida no contiene ``customer_id`` ni ninguna otra columna ajena a la
    lista ``feature_cols``.
    """
    from pyspark.sql import functions as F

    if len(feature_cols) != len(means) or len(feature_cols) != len(stds):
        raise ValueError("Features, medias y desviaciones deben tener la misma longitud.")
    spark = get_or_create_spark(spark)
    frame = spark.read.parquet(input_path).select(*feature_cols, target_col)
    for column in feature_cols:
        frame = frame.withColumn(column, F.col(column).cast("double"))
    frame = frame.fillna(float(fill_value), subset=list(feature_cols))
    for index, column in enumerate(feature_cols):
        std = max(float(stds[index]), 1e-8)
        frame = frame.withColumn(
            column,
            ((F.col(column) - F.lit(float(means[index]))) / F.lit(std)).cast(
                "float"
            ),
        )
    frame = frame.withColumn(target_col, F.col(target_col).cast("float")).filter(
        F.col(target_col).isNotNull()
    )
    frame = frame.select(*feature_cols, target_col)
    if repartitions > 0:
        frame = frame.repartition(int(repartitions))
    frame.write.mode(write_mode).option("compression", "snappy").parquet(output_path)
    return output_path


def prepare_train_validation(
    train_path: str,
    validation_path: str,
    prepared_train_path: str,
    prepared_validation_path: str,
    target_col: str,
    id_col: str,
    spark: Any | None = None,
    train_repartitions: int = 512,
    validation_repartitions: int = 256,
) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Prepara ambos splits usando estadísticas de train y devuelve metadatos."""
    spark = get_or_create_spark(spark)
    features = infer_numeric_features(train_path, target_col, id_col, spark)
    means, stds = compute_standardization_stats(train_path, features, spark)
    prepare_tabular_split(
        train_path,
        prepared_train_path,
        features,
        target_col,
        means,
        stds,
        spark,
        train_repartitions,
    )
    prepare_tabular_split(
        validation_path,
        prepared_validation_path,
        features,
        target_col,
        means,
        stds,
        spark,
        validation_repartitions,
    )
    return features, means, stds


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Separa una URI S3 en bucket y key."""
    if not uri.startswith("s3://"):
        raise ValueError(f"Se esperaba una URI s3://, recibido: {uri}")
    bucket, _, key = uri[5:].partition("/")
    if not bucket:
        raise ValueError(f"URI S3 inválida: {uri}")
    return bucket, key


def _s3_path(uri: str) -> str:
    bucket, key = parse_s3_uri(uri) if uri.startswith("s3://") else uri.split("/", 1)
    return f"{bucket}/{key}".rstrip("/")


def s3_filesystem(region: str = "us-east-1") -> pafs.S3FileSystem:
    """Crea un filesystem Arrow después de configurar certificados."""
    configure_ssl()
    return pafs.S3FileSystem(region=region)


def expand_parquet_paths(paths: Sequence[str], region: str = "us-east-1") -> list[str]:
    """Resuelve prefijos S3 a archivos Parquet, una vez en el driver."""
    filesystem = s3_filesystem(region)
    files: list[str] = []
    for raw_path in paths:
        path = _s3_path(raw_path)
        if path.endswith(".parquet"):
            info = filesystem.get_file_info([path])[0]
            if info.type != pafs.FileType.File:
                raise FileNotFoundError(raw_path)
            files.append(path)
            continue
        infos = filesystem.get_file_info(pafs.FileSelector(path, recursive=True))
        files.extend(
            info.path
            for info in infos
            if info.type == pafs.FileType.File and info.path.endswith(".parquet")
        )
    files = sorted(set(files))
    if not files:
        raise FileNotFoundError(f"No se encontraron Parquet en: {list(paths)}")
    return files


def class_counts(
    prepared_path: str,
    target_col: str,
    spark: Any | None = None,
) -> tuple[int, int]:
    """Cuenta negativos y positivos del target binario en Spark."""
    from pyspark.sql import functions as F

    spark = get_or_create_spark(spark)
    counts = spark.read.parquet(prepared_path).agg(
        F.sum(F.when(F.col(target_col) == 0, 1).otherwise(0)).alias("negative"),
        F.sum(F.when(F.col(target_col) == 1, 1).otherwise(0)).alias("positive"),
        F.sum(
            F.when(~F.col(target_col).isin(0, 1), 1).otherwise(0)
        ).alias("invalid"),
    ).first()
    if counts["invalid"]:
        raise ValueError(
            "{} debe ser binario (0/1); se encontraron {} filas con otro valor.".format(
                target_col, counts["invalid"]
            )
        )
    return int(counts["negative"] or 0), int(counts["positive"] or 0)


def _local_path(s3_path: str, cache_dir: str) -> str:
    digest = hashlib.md5(s3_path.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, f"{digest}_{os.path.basename(s3_path)}")


def _download_to_cache(s3_path: str, cache_dir: str, region: str) -> str:
    """Descarga con boto3 para evitar fallas SSL de Arrow dentro de DDP."""
    local_path = _local_path(s3_path, cache_dir)
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return local_path
    import boto3

    bucket, key = s3_path.split("/", 1)
    os.makedirs(cache_dir, exist_ok=True)
    temporary = f"{local_path}.tmp.{os.getpid()}"
    boto3.client("s3", region_name=region).download_file(bucket, key, temporary)
    os.replace(temporary, local_path)
    return local_path


def _record_batch_to_numpy(
    record_batch: pa.RecordBatch, feature_cols: Sequence[str], target_col: str
) -> tuple[np.ndarray, np.ndarray]:
    features = []
    for column in feature_cols:
        array = pc.fill_null(
            pc.cast(record_batch.column(column), pa.float32(), safe=False), 0.0
        )
        features.append(array.to_numpy(zero_copy_only=False))
    x = np.column_stack(features).astype(np.float32, copy=False)
    target = pc.fill_null(
        pc.cast(record_batch.column(target_col), pa.float32(), safe=False), 0.0
    ).to_numpy(zero_copy_only=False)
    return x, target.astype(np.float32, copy=False)


class ArrowTabularBatchDataset(IterableDataset):
    """Dataset lazy: cada rank/worker procesa archivos Parquet distintos."""

    def __init__(
        self,
        files: Sequence[str],
        feature_cols: Sequence[str],
        target_col: str,
        rank: int,
        world_size: int,
        batch_rows: int,
        train_batch_size: int,
        shuffle: bool,
        cache_dir: str,
        region: str,
    ) -> None:
        super().__init__()
        self.files = sorted(files)
        self.feature_cols = list(feature_cols)
        self.target_col = target_col
        self.rank = rank
        self.world_size = world_size
        self.batch_rows = batch_rows
        self.train_batch_size = train_batch_size
        self.shuffle = shuffle
        self.cache_dir = cache_dir
        self.region = region

    def __iter__(self) -> Iterable[dict[str, torch.Tensor]]:
        configure_ssl()
        worker = get_worker_info()
        worker_id = worker.id if worker else 0
        num_workers = worker.num_workers if worker else 1
        shard_count = max(self.world_size, 1) * num_workers
        shard_id = self.rank * num_workers + worker_id
        shard = self.files[shard_id::shard_count]
        if not shard:
            return
        cache_dir = os.path.join(self.cache_dir, f"rank_{self.rank}", f"worker_{worker_id}")
        local_files = [_download_to_cache(path, cache_dir, self.region) for path in shard]
        scanner = ds.dataset(local_files, format="parquet").scanner(
            columns=self.feature_cols + [self.target_col],
            batch_size=self.batch_rows,
            use_threads=True,
        )
        for record_batch in scanner.to_batches():
            x, y = _record_batch_to_numpy(record_batch, self.feature_cols, self.target_col)
            indices = np.arange(len(y))
            if self.shuffle:
                np.random.shuffle(indices)
            for start in range(0, len(y), self.train_batch_size):
                selected = indices[start : start + self.train_batch_size]
                yield {
                    "x": torch.from_numpy(np.ascontiguousarray(x[selected])),
                    "y": torch.from_numpy(np.ascontiguousarray(y[selected])),
                }


def make_loader(
    files: Sequence[str],
    feature_cols: Sequence[str],
    target_col: str,
    rank: int,
    world_size: int,
    config: dict[str, Any],
    shuffle: bool,
) -> DataLoader:
    """Construye un DataLoader cuyo dataset ya entrega mini-batches."""
    dataset = ArrowTabularBatchDataset(
        files=files,
        feature_cols=feature_cols,
        target_col=target_col,
        rank=rank,
        world_size=world_size,
        batch_rows=int(config["batch_rows"]),
        train_batch_size=int(config["batch_size"]),
        shuffle=shuffle,
        cache_dir=config["cache_dir"],
        region=config["region"],
    )
    workers = int(config.get("num_workers", 0))
    return DataLoader(
        dataset,
        batch_size=None,
        num_workers=workers,
        persistent_workers=workers > 0,
        pin_memory=False,
    )


class TabularMLP(nn.Module):
    """MLP binario para variables numéricas; no contiene embeddings de IDs."""

    def __init__(
        self,
        num_features: int,
        hidden_dims: Sequence[int],
        dropout: float,
        activation: str = "relu",
        use_batch_norm: bool = True,
    ) -> None:
        super().__init__()
        activations = {"relu": nn.ReLU, "gelu": nn.GELU, "silu": nn.SiLU}
        if activation not in activations:
            raise ValueError(f"Activación no soportada: {activation}")
        layers: list[nn.Module] = []
        previous = num_features
        for width in hidden_dims:
            layers.append(nn.Linear(previous, int(width)))
            if use_batch_norm:
                layers.append(nn.BatchNorm1d(int(width)))
            layers.append(activations[activation]())
            if dropout > 0:
                layers.append(nn.Dropout(dropout))
            previous = int(width)
        layers.append(nn.Linear(previous, 1))
        self.network = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x).squeeze(1)


def suggest_mlp_params(trial: Any) -> dict[str, Any]:
    """Espacio de búsqueda de Optuna para la arquitectura y optimización."""
    layers = trial.suggest_int("n_layers", 1, 4)
    return {
        "hidden_dims": [
            trial.suggest_int(f"units_layer_{index}", 32, 512, step=32)
            for index in range(layers)
        ],
        "dropout": trial.suggest_float("dropout", 0.0, 0.5),
        "activation": trial.suggest_categorical("activation", ["relu", "gelu", "silu"]),
        "use_batch_norm": trial.suggest_categorical("use_batch_norm", [True, False]),
        "learning_rate": trial.suggest_float("learning_rate", 1e-4, 3e-3, log=True),
        "weight_decay": trial.suggest_float("weight_decay", 1e-7, 1e-3, log=True),
    }


def _distributed_info() -> tuple[int, int]:
    rank = int(os.environ.get("RANK", os.environ.get("OMPI_COMM_WORLD_RANK", 0)))
    world_size = int(os.environ.get("WORLD_SIZE", os.environ.get("OMPI_COMM_WORLD_SIZE", 1)))
    return rank, world_size


def _setup_distributed() -> tuple[int, int]:
    rank, world_size = _distributed_info()
    if world_size > 1 and not dist.is_initialized():
        dist.init_process_group(backend="gloo")
    return rank, world_size


def _cleanup_distributed() -> None:
    if dist.is_available() and dist.is_initialized():
        try:
            dist.barrier()
        except Exception:
            pass
        try:
            dist.destroy_process_group()
        except Exception:
            pass


def _reduce(value: float, device: torch.device) -> float:
    tensor = torch.tensor(value, dtype=torch.float64, device=device)
    if dist.is_available() and dist.is_initialized():
        dist.all_reduce(tensor, op=dist.ReduceOp.SUM)
    return float(tensor.item())


def _train_epoch(
    model: nn.Module,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
    device: torch.device,
) -> float:
    model.train()
    total_loss, total_rows = 0.0, 0
    join = model.join() if isinstance(model, DDP) else nullcontext()
    with join:
        for batch in loader:
            x, y = batch["x"].to(device), batch["y"].to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(x), y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(y)
            total_rows += len(y)
    return _reduce(total_loss, device) / max(_reduce(total_rows, device), 1.0)


@torch.no_grad()
def _evaluate(model: nn.Module, loader: DataLoader, device: torch.device) -> dict[str, float]:
    model.eval()
    tp = tn = fp = fn = 0
    for batch in loader:
        x, y = batch["x"].to(device), batch["y"].to(device).long()
        prediction = (torch.sigmoid(model(x)) >= 0.5).long()
        tp += int(((prediction == 1) & (y == 1)).sum())
        tn += int(((prediction == 0) & (y == 0)).sum())
        fp += int(((prediction == 1) & (y == 0)).sum())
        fn += int(((prediction == 0) & (y == 1)).sum())
    tp, tn, fp, fn = (int(_reduce(value, device)) for value in (tp, tn, fp, fn))
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    return {
        "precision": precision,
        "recall": recall,
        "f1_score": 2 * precision * recall / max(precision + recall, 1e-12),
        "accuracy": (tp + tn) / max(tp + tn + fp + fn, 1),
    }


def _save_artifact(payload: dict[str, Any], path: str, region: str) -> str:
    if path.startswith("s3://"):
        import boto3

        buffer = io.BytesIO()
        torch.save(payload, buffer)
        bucket, key = parse_s3_uri(path)
        boto3.client("s3", region_name=region).put_object(
            Bucket=bucket, Key=key, Body=buffer.getvalue()
        )
    else:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        torch.save(payload, path)
    return path


def run_distributed_training(
    train_files: Sequence[str],
    validation_files: Sequence[str],
    feature_cols: Sequence[str],
    target_col: str,
    model_params: dict[str, Any],
    training_config: dict[str, Any],
    artifact_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Función picklable para ``run_with_torch_distributor``.

    Cada llamada entrena una arquitectura en DDP; Optuna la invoca por trial.
    """
    configure_ssl()
    rank, world_size = _setup_distributed()
    try:
        set_seed(int(training_config["seed"]) + rank)
        if len(train_files) < world_size:
            raise ValueError("Hay menos archivos train que procesos DDP.")
        device = torch.device("cpu")
        train_loader = make_loader(
            train_files, feature_cols, target_col, rank, world_size, training_config, True
        )
        validation_loader = make_loader(
            validation_files, feature_cols, target_col, rank, world_size, training_config, False
        )
        architecture_params = {
            key: model_params[key]
            for key in ("hidden_dims", "dropout", "activation", "use_batch_norm")
        }
        base_model = TabularMLP(len(feature_cols), **architecture_params).to(device)
        model: nn.Module = DDP(base_model) if world_size > 1 else base_model
        criterion = nn.BCEWithLogitsLoss(
            pos_weight=torch.tensor([float(training_config["pos_weight"])], device=device)
        )
        optimizer = torch.optim.AdamW(
            model.parameters(),
            lr=float(model_params["learning_rate"]),
            weight_decay=float(model_params["weight_decay"]),
        )
        history = [
            _train_epoch(model, train_loader, optimizer, criterion, device)
            for _ in range(int(training_config["epochs"]))
        ]
        metrics = _evaluate(model, validation_loader, device)
        if rank != 0:
            return {"status": "worker_done"}
        result: dict[str, Any] = {
            "status": "ok",
            "train_loss": history[-1],
            "valid_metrics": metrics,
        }
        artifact_path = training_config.get("artifact_path")
        if artifact_path:
            payload = {
                "model_state_dict": base_model.state_dict(),
                "feature_cols": list(feature_cols),
                "target_col": target_col,
                "model_params": model_params,
                "architecture_params": architecture_params,
                **(artifact_metadata or {}),
            }
            result["artifact_path"] = _save_artifact(
                payload, artifact_path, training_config["region"]
            )
        return result
    finally:
        _cleanup_distributed()
