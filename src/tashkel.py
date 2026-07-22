from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
import pandas as pd


if __name__ == "__main__":

  text = "كيف الحال"

  model_name = "basharalrfooh/Fine-Tashkeel"

  tokenizer = AutoTokenizer.from_pretrained(model_name)
  model = AutoModelForSeq2SeqLM.from_pretrained(model_name)

  input_ids = tokenizer(text, return_tensors="pt").input_ids
  outputs = model.generate(input_ids, max_new_tokens=128)
  decoded_output = tokenizer.decode(outputs[0], skip_special_tokens=True, clean_up_tokenization_spaces=False)
  print("Generated output:", decoded_output)
