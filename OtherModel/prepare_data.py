import os
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import torch
import nlp
from transformers import T5Tokenizer, BartTokenizer, GPT2Tokenizer, HfArgumentParser


logger = logging.getLogger(__name__)


@dataclass
class DataTrainingArguments:
    """
    Arguments pertaining to what data we are going to input our model for training and eval.
    """
    task: str = field(
        metadata={"help": "Which task 'ans_ext', 'qg'"},
    )
    model_type: str = field(metadata={"help": "One of 't5', 'bart', gpt"})
    train_cache_path: Optional[str] = field(
        default=None,
        metadata={"help": "name for cached train dataset"},
    )
    eval_cache_path: Optional[str] = field(
        default=None,
        metadata={"help": "name for cached eval dataset"},
    )
    train_path: str = field(
        default="train_task_data.json",
        metadata={"help": "Path for train dataset"}, 
    )
    eval_path: str = field(
        default="dev_task_data.json",
        metadata={"help": "Path for eval dataset"},
    )
    qg_format: Optional[str] = field(
        default='highlight_qg_format',
        metadata={"help": "How to format inputs for que generation, 'highlight_qg_format' or 'prepend_qg_format'"}, 
    )
    max_source_length: Optional[int] = field(
        default=512,
        metadata={"help": "Max input length for the source text"},
    )
    max_target_length: Optional[int] = field(
        default=32,
        metadata={"help": "Max input length for the target text"},
    )

@property
def format_dataset(data, dtype, columns):
    return {
        "type": self._format_type,
        "format_kwargs": {},
        "columns": self.column_names if self._format_columns is None else self._format_columns,
        "output_all_columns": self._output_all_columns,
    }

def dataset_to_batch(dataset, batch_size=1000) -> List[List]:
    """ Convert a list to list of batches """
    for i in range(0, len(dataset), batch_size):  
        yield {'source_text': [x['source_text'] for x in dataset[i:i + batch_size]], 'target_text': [x['target_text'] for x in dataset[i:i + batch_size]], 'task': [x['task'] for x in dataset[i:i + batch_size]]}

def handle_special_token(tokenizer, model_type):
    
    if model_type == "t5":
        # tokenizer.bos_token = "<s>"
        # tokenizer.eos_token = "</s>" # already exist
        # tokenizer.sep_token = "<sep>"
        tokenizer.add_special_tokens({'sep_token': '<sep>', "bos_token": "<s>"})

    elif model_type == "gpt2":
        # tokenizer.bos_token = "<|endoftext|>" # already exist
        # tokenizer.eos_token = "<|endoftext|>" # already exist
        tokenizer.add_special_tokens({'sep_token': '<sep>', 'pad_token': '[PAD]'})

    elif model_type == "bart":
        # tokenizer.bos_token = "<s>"
        # tokenizer.eos_token = "</s>" # already exist
        # tokenizer.sep_token = "</s>"
        pass

    # tokenizer.add_tokens([tokenizer.bos_token, tokenizer.eos_token, tokenizer.sep_token])

    # Add special tokens for highlighting input format
    # consider use <hl> and <\hl>
    tokenizer.add_tokens(['<hl>'])


class DataProcessor:
    def __init__(self, tokenizer, model_type="t5", max_source_length=512, max_target_length=32):
        """
        Instantiate a Dataprocessor object
        """
        self.tokenizer = tokenizer
        self.max_source_length = max_source_length
        self.max_target_length = max_target_length
        self.model_type = model_type
        self.hl_token = "<hl>"
        
        self.sep_token = tokenizer.sep_token
        self.bos_token = tokenizer.bos_token
        self.eos_token = tokenizer.eos_token
        # if model_type == "t5":
        #     self.sep_token = tokenizer.sep_token
        # elif model_type == "bart":
        #     self.sep_token = "<sep>"
        # elif model_type == "gpt2":
        #     self.sep_token = "<sep>"
        # else:
        #     raise NotImplementedError("not for other models yet")
        #     # self.sep_token = "[SEP]"

    def _unnest(self, dataset):
        """ Serialize a batch tokenized dataset """
        dataset = [
                        [
                            {
                                'source_ids': torch.tensor(batch['source_ids'][x]),
                                'target_ids': torch.tensor(batch['target_ids'][x]),
                                'attention_mask': torch.tensor(batch['attention_mask'][x])
                            }
                            for x in range(len(batch['source_ids']))
                        ]
                        for batch in dataset
                    ]

        # Flatten list of lists
        dataset = [y for subl in dataset for y in subl]

        return dataset
  
    def process(self, dataset):

        if self.model_type == "t5":
            dataset = list(map(self._t5_add_task_information, dataset))
            dataset = list(map(self._add_eos_for_target, dataset))
        elif self.model_type == "gpt2":
            dataset = list(map(self._add_eos_for_source, dataset))
            dataset = list(map(self._add_eos_for_target, dataset))
        elif self.model_type == "bart":
            dataset = list(map(self._add_eos_for_target, dataset))
        
        dataset = list(map(self._add_special_tokens, dataset))

        # breakpoint()
        dataset = list(map(self._convert_to_features, list(dataset_to_batch(dataset))))
        dataset = self._unnest(dataset)
        return dataset

    def _add_eos_examples_gpt2(self, example):
        """
        Append an eos token to each source and target string
        """
        example['source_text'] = example['source_text'] + self.tokenizer.eos_token
        example['target_text'] = example['target_text'] + self.tokenizer.eos_token
        return example

    def _t5_add_task_information(self, example):
        """
        task information for T5
        """
        task_text = "Generate question from context:"
        example['source_text'] = task_text + " " + example['source_text']
        # example['target_text'] = example['target_text'] + self.tokenizer.eos_token
        return example

    def _add_eos_for_source(self, example):
        """
        Append an eos token to each source and target string
        """
        example['source_text'] = example['source_text'] + self.eos_token
        return example

    def _add_eos_for_target(self, example):
        """
        Append an eos token to each source and target string
        """
        example['target_text'] = example['target_text'] + self.eos_token
        return example
  
    def _add_special_tokens(self, example):
        """
        Replace special token placeholders with their respective token strings
        """
        example['source_text'] = example['source_text'].replace("{hl_token}", self.hl_token)    
        example['target_text'] = example['target_text'].replace("{sep_token}", self.sep_token)
        return example
  
    def _convert_to_features(self, example_batch):
        """
        Tokenize the examples
        """
        source_encoding = self.tokenizer.batch_encode_plus(
            example_batch['source_text'],
            max_length=self.max_source_length,
            padding='max_length',
            pad_to_max_length=True,
            truncation=True, 
        )
        target_encoding = self.tokenizer.batch_encode_plus(
            example_batch['target_text'],
            max_length=self.max_target_length,
            padding='max_length',
            pad_to_max_length=True,
            truncation=True, 
        )

        encodings = {
            'source_ids': source_encoding['input_ids'], 
            'target_ids': target_encoding['input_ids'],
            'attention_mask': source_encoding['attention_mask'],
        }

        return encodings
     

if __name__ == "__main__":
    # Load training args
    parser = HfArgumentParser((DataTrainingArguments,))
    data_args = parser.parse_json_file('data_args.json')[0]

    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s -   %(message)s",
        datefmt="%m/%d/%Y %H:%M:%S",
        level=logging.INFO
    )
  
    # Supports t5， bart and gpt2 tokenizers
    if data_args.model_type == 't5':
        tokenizer = T5Tokenizer.from_pretrained("t5-base")
    elif data_args.model_type == 'bart':
        tokenizer = BartTokenizer.from_pretrained("facebook/bart-base")
    elif data_args.model_type == 'gpt2':
        tokenizer = GPT2Tokenizer.from_pretrained("gpt2")
        # tokenizer.pad_token = tokenizer.eos_token # not sure why Aaron use eos to pad.
    else:
        raise NotImplementedError("Not yet")

    handle_special_token(tokenizer, model_type=data_args.model_type)
    
    print(data_args.train_path, data_args.eval_path)
    with open('data/' + data_args.train_path, 'r') as f:
        train_dataset = json.load(f)
    with open('data/' + data_args.eval_path, 'r') as f:
        eval_dataset = json.load(f)

    processor = DataProcessor(
        tokenizer,
        model_type=data_args.model_type,
        max_source_length=data_args.max_source_length,
        max_target_length=data_args.max_target_length
    )

    train_dataset = train_dataset[data_args.task]
    eval_dataset = eval_dataset[data_args.task]
    train_dataset = processor.process(train_dataset)
    eval_dataset = processor.process(eval_dataset)

    train_file_name = f"train_data_{data_args.task}_{data_args.qg_format}_{data_args.model_type}.pt"
    eval_file_name = f"eval_data_{data_args.task}_{data_args.qg_format}_{data_args.model_type}.pt"
    if data_args.train_cache_path is None:
        train_path = os.path.join("data", train_file_name)
    else:
        train_path = os.path.join(data_args.train_cache_path, train_file_name)
    if data_args.eval_cache_path is None:
        eval_path = os.path.join("data", eval_file_name)
    else:
        eval_path = os.path.join(eval_cache_path, data_args.eval_file_name)
    
    # Save the preprocessed training data
    torch.save(train_dataset, train_path)
    logger.info(f"saving train dataset at {train_path}")
    torch.save(eval_dataset, eval_path)
    logger.info(f"saving eval dataset at {eval_path}")
    
    tokenizer_path = f"{data_args.model_type}_qg_tokenizer"
    if not os.path.exists(tokenizer_path):
        os.mkdir(tokenizer_path)
    tokenizer.save_pretrained(tokenizer_path)
    logger.info(f"saved tokenizer at {tokenizer_path}")
