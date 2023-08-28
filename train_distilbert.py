# Databricks notebook source
# MAGIC %md # 02c-Fine Tune distilbert-base-cased-distilled-squad

# COMMAND ----------

# MAGIC %md
# MAGIC Install these additional NVIDIA libraries for Databricks Runtime 13.x+ ML:

# COMMAND ----------

!wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/libcusparse-dev-11-7_11.7.3.50-1_amd64.deb -O /tmp/libcusparse-dev-11-7_11.7.3.50-1_amd64.deb && \
  wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/libcublas-dev-11-7_11.10.1.25-1_amd64.deb -O /tmp/libcublas-dev-11-7_11.10.1.25-1_amd64.deb && \
  wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/libcusolver-dev-11-7_11.4.0.1-1_amd64.deb -O /tmp/libcusolver-dev-11-7_11.4.0.1-1_amd64.deb && \
  wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/libcurand-dev-11-7_10.2.10.91-1_amd64.deb -O /tmp/libcurand-dev-11-7_10.2.10.91-1_amd64.deb && \
  dpkg -i /tmp/libcusparse-dev-11-7_11.7.3.50-1_amd64.deb && \
  dpkg -i /tmp/libcublas-dev-11-7_11.10.1.25-1_amd64.deb && \
  dpkg -i /tmp/libcusolver-dev-11-7_11.4.0.1-1_amd64.deb && \
  dpkg -i /tmp/libcurand-dev-11-7_10.2.10.91-1_amd64.deb

# COMMAND ----------

# MAGIC %pip install -r requirements.txt

# COMMAND ----------

# MAGIC %load_ext autoreload
# MAGIC %autoreload 2

# COMMAND ----------

import logging

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s", level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S"
)
logging.getLogger("py4j").setLevel(logging.WARNING)
logging.getLogger("sh.command").setLevel(logging.ERROR)

# COMMAND ----------

import os
import re
from datetime import datetime
from training.consts import DEFAULT_INPUT_MODEL, SUGGESTED_INPUT_MODELS
from training.trainer import load_training_dataset, load_tokenizer

dbutils.widgets.combobox("input_model", DEFAULT_INPUT_MODEL, SUGGESTED_INPUT_MODELS, "input_model")
dbutils.widgets.text("num_gpus", "", "num_gpus")
dbutils.widgets.text("local_training_root", "", "local_training_root")
dbutils.widgets.text("dbfs_output_root", "", "dbfs_output_root")
dbutils.widgets.text("experiment_id", "", "experiment_id")
dbutils.widgets.combobox("gpu_family", "a100", ["v100", "a10", "a100"])

# COMMAND ----------

# DBTITLE 1,use custom training set
dataset = load_training_dataset("/dbfs/mnt/datalake/tower_contract_abstraction/fine-tuning/")
print(dataset[0]["text"])

# COMMAND ----------

timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
model_name = "distilbert"

experiment_id = dbutils.widgets.get("experiment_id")
# input_model = dbutils.widgets.get("input_model")
input_model = "distilbert-base-cased-distilled-squad"

if experiment_id:
    experiment_id = re.sub(r"\s+", "_", experiment_id.strip())
    model_name = f"{model_name}__{experiment_id}"

checkpoint_dir_name = f"{model_name}__{timestamp}"

distilbert_training_dir_name = "distilbert_training"

# Use the local training root path if it was provided.  Otherwise try to find a sensible default.
local_training_root = dbutils.widgets.get("local_training_root")
if not local_training_root:
    # Use preferred path when working in a Databricks cluster if it exists.
    if os.path.exists("/local_disk0"):
        local_training_root = os.path.join("/local_disk0", distilbert_training_dir_name)
    # Otherwise use the home directory.
    else:
        local_training_root = os.path.join(os.path.expanduser('~'), distilbert_training_dir_name)

dbfs_output_root = dbutils.widgets.get("dbfs_output_root")
if not dbfs_output_root:
    dbfs_output_root = f"/dbfs/{distilbert_training_dir_name}"

os.makedirs(local_training_root, exist_ok=True)
os.makedirs(dbfs_output_root, exist_ok=True)

local_output_dir = os.path.join(local_training_root, checkpoint_dir_name)
dbfs_output_dir = os.path.join(dbfs_output_root, checkpoint_dir_name)
tensorboard_display_dir = f"{local_output_dir}/runs"

print(f"Local Output Dir: {local_output_dir}")
print(f"DBFS Output Dir: {dbfs_output_dir}")
print(f"Tensorboard Display Dir: {tensorboard_display_dir}")

# pick an appropriate config file
gpu_family = dbutils.widgets.get("gpu_family")
config_file_name = f"{gpu_family}_config.json"
deepspeed_config = os.path.join(os.getcwd(), "config", config_file_name)
print(f"Deepspeed config file: {deepspeed_config}")

# configure the batch_size
batch_size = 3
if gpu_family == "a10":
    batch_size = 4
elif gpu_family == "a100":
    batch_size = 6

# configure num_gpus, if specified
num_gpus_flag = ""
num_gpus = dbutils.widgets.get("num_gpus")
if num_gpus:
    num_gpus = int(num_gpus)
    num_gpus_flag = f"--num_gpus={num_gpus}"

if gpu_family == "v100":
    bf16_flag = "--bf16 false"
else:
    bf16_flag = "--bf16 true"

os.environ["TOKENIZERS_PARALLELISM"] = "false"

# COMMAND ----------

!deepspeed {num_gpus_flag} \
    --module training.trainer \
    --input-model {input_model} \
    --training-dataset /dbfs/mnt/datalake/tower_contract_abstraction/fine-tuning/ \
    --deepspeed {deepspeed_config} \
    --epochs 2 \
    --local-output-dir {local_output_dir} \
    --dbfs-output-dir {dbfs_output_dir} \
    --per-device-train-batch-size {batch_size} \
    --per-device-eval-batch-size {batch_size} \
    --logging-steps 10 \
    --save-steps 200 \
    --save-total-limit 20 \
    --eval-steps 50 \
    --warmup-steps 50 \
    --test-size 200 \
    --lr 5e-6 \
    {bf16_flag}

# COMMAND ----------

from training.generate import generate_response, load_model_tokenizer_for_generate

model, tokenizer = load_model_tokenizer_for_generate(dbfs_output_dir)

# COMMAND ----------

# Examples from https://www.databricks.com/blog/2023/03/24/hello-dolly-democratizing-magic-chatgpt-open-models.html
instructions = [
    "Write a love letter to Edgar Allan Poe.",
    "Write a tweet announcing Dolly, a large language model from Databricks.",
    "I'm selling my Nikon D-750, write a short blurb for my ad.",
    "Explain to me the difference between nuclear fission and fusion.",
    "Give me a list of 5 science fiction books I should read next.",
]

# set some additional pipeline args
pipeline_kwargs = {'torch_dtype': "auto"}
if gpu_family == "v100":
    pipeline_kwargs['torch_dtype'] = "float16"
elif gpu_family == "a10" or gpu_family == "a100":
    pipeline_kwargs['torch_dtype'] = "bfloat16"

# Use the model to generate responses for each of the instructions above.
for instruction in instructions:
    response = generate_response(instruction, model=model, tokenizer=tokenizer, **pipeline_kwargs)
    if response:
        print(f"Instruction: {instruction}\n\n{response}\n\n-----------\n")
