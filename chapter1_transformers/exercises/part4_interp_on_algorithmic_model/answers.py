#%%
import os; os.environ["ACCELERATE_DISABLE_RICH"] = "1"
import sys
from functools import partial
import json
from typing import List, Tuple, Union, Optional, Callable, Dict
import torch as t
from torch import Tensor
from sklearn.linear_model import LinearRegression
import numpy as np
from plotly.subplots import make_subplots
import plotly.express as px
import plotly.graph_objects as go
import einops
from tqdm import tqdm
from jaxtyping import Float, Int, Bool
from pathlib import Path
import pandas as pd
import circuitsvis as cv
import webbrowser
from IPython.display import display
from transformer_lens import utils, ActivationCache, HookedTransformer, HookedTransformerConfig
from transformer_lens.hook_points import HookPoint
from transformer_lens.components import LayerNorm

# Make sure exercises are in the path
chapter = r"chapter1_transformers"
exercises_dir = Path(f"{os.getcwd().split(chapter)[0]}/{chapter}/exercises").resolve()
section_dir = exercises_dir / "part4_interp_on_algorithmic_model"
if str(exercises_dir) not in sys.path: sys.path.append(str(exercises_dir))

import plotly_utils
from plotly_utils import hist, bar, imshow
import part4_interp_on_algorithmic_model.tests as tests
from part4_interp_on_algorithmic_model.brackets_datasets import SimpleTokenizer, BracketsDataset

device = t.device("cuda" if t.cuda.is_available() else "cpu")

MAIN = __name__ == "__main__"


#%%
VOCAB = "()"

cfg = HookedTransformerConfig(
    n_ctx=42,
    d_model=56,
    d_head=28,
    n_heads=2,
    d_mlp=56,
    n_layers=3,
    attention_dir="bidirectional", # defaults to "causal"
    act_fn="relu",
    d_vocab=len(VOCAB)+3, # plus 3 because of end and pad and start token
    d_vocab_out=2, # 2 because we're doing binary classification
    use_attn_result=True, 
    device=device,
    use_hook_tokens=True
)

model = HookedTransformer(cfg).eval()

state_dict = t.load(section_dir / "brackets_model_state_dict.pt")
model.load_state_dict(state_dict)

#%%
tokenizer = SimpleTokenizer("()")

# Examples of tokenization
# (the second one applies padding, since the sequences are of different lengths)
print(tokenizer.tokenize("()"))
print(tokenizer.tokenize(["()", "()()"]))

# Dictionaries mapping indices to tokens and vice versa
print(tokenizer.i_to_t)
print(tokenizer.t_to_i)

# Examples of decoding (all padding tokens are removed)
print(tokenizer.decode(t.tensor([[0, 3, 4, 2, 1, 1]])))

#%%
def add_perma_hooks_to_mask_pad_tokens(model: HookedTransformer, pad_token: int) -> HookedTransformer:

    # Hook which operates on the tokens, and stores a mask where tokens equal [pad]
    def cache_padding_tokens_mask(tokens: Float[Tensor, "batch seq"], hook: HookPoint) -> None:
        hook.ctx["padding_tokens_mask"] = einops.rearrange(tokens == pad_token, "b sK -> b 1 1 sK")

    # Apply masking, by referencing the mask stored in the `hook_tokens` hook context
    def apply_padding_tokens_mask(
        attn_scores: Float[Tensor, "batch head seq_Q seq_K"],
        hook: HookPoint,
    ) -> None:
        attn_scores.masked_fill_(model.hook_dict["hook_tokens"].ctx["padding_tokens_mask"], -1e5)
        if hook.layer() == model.cfg.n_layers - 1:
            del model.hook_dict["hook_tokens"].ctx["padding_tokens_mask"]

    # Add these hooks as permanent hooks (i.e. they aren't removed after functions like run_with_hooks)
    for name, hook in model.hook_dict.items():
        if name == "hook_tokens":
            hook.add_perma_hook(cache_padding_tokens_mask)
        elif name.endswith("attn_scores"):
            hook.add_perma_hook(apply_padding_tokens_mask)

    return model


model.reset_hooks(including_permanent=True)
model = add_perma_hooks_to_mask_pad_tokens(model, tokenizer.PAD_TOKEN)

#%%
N_SAMPLES = 5000
with open(section_dir / "brackets_data.json") as f:
    data_tuples: List[Tuple[str, bool]] = json.load(f)
    print(f"loaded {len(data_tuples)} examples")
assert isinstance(data_tuples, list)
data_tuples = data_tuples[:N_SAMPLES]
data = BracketsDataset(data_tuples).to(device)
data_mini = BracketsDataset(data_tuples[:100]).to(device)

#%%
hist(
    [len(x) for x, _ in data_tuples], 
    nbins=data.seq_length,
    title="Sequence lengths of brackets in dataset",
    labels={"x": "Seq len"}
)

#%%
# Define and tokenize examples
examples = ["()()", "(())", "))((", "()", "((()()()()))", "(()()()(()(())()", "()(()(((())())()))"]
labels = [True, True, False, True, True, False, True]
toks = tokenizer.tokenize(examples)

# Get output logits for the 0th sequence position (i.e. the [start] token)
logits = model(toks)[:, 0]

# Get the probabilities via softmax, then get the balanced probability (which is the second element)
prob_balanced = logits.softmax(-1)[:, 1]

# Display output
printed_strings = [
    f"{ex:18} : {prob:<8.4%} : label={int(label)}"
    for ex, prob, label in
    zip(examples, prob_balanced, labels)
]
print("Model confidence:\n" + "\n".join(printed_strings))

#%%
def run_model_on_data(model: HookedTransformer, data: BracketsDataset, batch_size: int = 200) -> Float[Tensor, "batch 2"]:
    '''Return probability that each example is balanced'''
    all_logits = []
    for i in tqdm(range(0, len(data.strs), batch_size)):
        toks = data.toks[i : i + batch_size]
        logits = model(toks)[:, 0]
        all_logits.append(logits)
    all_logits = t.cat(all_logits)
    assert all_logits.shape == (len(data), 2)
    return all_logits


test_set = data
n_correct = (run_model_on_data(model, test_set).argmax(-1).bool() == test_set.isbal).sum()
print(f"\nModel got {n_correct} out of {len(data)} training examples correct!")

#%%
def is_balanced_forloop(parens: str) -> bool:
    '''
    Return True if the parens are balanced.

    Parens is just the ( and ) characters, no begin or end tokens.
    '''
    counter = 0
    for c in parens:
        if c == "(":
            counter += 1
        elif c == ")":
            counter -= 1
        if counter < 0:
            return False
    return counter == 0


for (parens, expected) in zip(examples, labels):
    actual = is_balanced_forloop(parens)
    assert expected == actual, f"{parens}: expected {expected} got {actual}"
print("is_balanced_forloop ok!")

#%%
relu = t.nn.functional.relu

def is_balanced_vectorized(tokens: Float[Tensor, "seq_len"]) -> bool:
    '''
    Return True if the parens are balanced.

    tokens is a vector which has start/pad/end indices (0/1/2) as well as left/right brackets (3/4)
    '''
    token_lookup = lambda x: relu(x-2) - 3*relu(x-3)
    vectorized_tokens = token_lookup(tokens).float()
    M = t.tril(t.ones(vectorized_tokens.shape[0], vectorized_tokens.shape[0]))
    cum_sum = M @ vectorized_tokens
    no_negatives = not (relu(-1 * cum_sum).sum().abs() > 1e-2).item()
    ends_with_zero = cum_sum[tokens == tokenizer.END_TOKEN].abs().item() <= 1e-2
    return no_negatives and ends_with_zero


for (tokens, expected) in zip(tokenizer.tokenize(examples), labels):
    actual = is_balanced_vectorized(tokens)
    assert expected == actual, f"{tokens}: expected {expected} got {actual}"
print("is_balanced_vectorized ok!")

#%%
def get_post_final_ln_dir(model: HookedTransformer) -> Float[Tensor, "d_model"]:
    '''
    Returns the direction in which final_ln_output[0, :] should point to maximize P(unbalanced)
    '''
    return model.W_U[:, 0] - model.W_U[:, 1]

tests.test_get_post_final_ln_dir(get_post_final_ln_dir, model)

#%%
def get_activations(
    model: HookedTransformer, 
    toks: Int[Tensor, "batch seq"], 
    names: Union[str, List[str]]
) -> Union[t.Tensor, ActivationCache]:
    '''
    Uses hooks to return activations from the model.

    If names is a string, returns the activations for that hook name.
    If names is a list of strings, returns a dictionary mapping hook names to tensors of activations.
    '''
    names_list = [names] if isinstance(names, str) else names
    _, cache = model.run_with_cache(
        toks,
        return_type=None,
        names_filter=lambda name: name in names_list,
    )

    return cache[names] if isinstance(names, str) else cache
