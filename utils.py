import tiktoken
import openai
import os
import backoff

from llama_index import Document

from pubmed_api import pubmed_api
from gene_api import gene_api
from functools import partial

MAX_TOKENS = 4097

api_info_mapping = {"mygene": gene_api, "PubMed": pubmed_api}


openai.api_key = os.environ['OPENAI_API_KEY']
openai.organization = os.environ['OPENAI_ORG']


def num_tokens_from_string(string: str, encoding_name: str = 'gpt2') -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens

def get_max_completion_len(prompt):
    tokens = num_tokens_from_string(prompt)
    return MAX_TOKENS - tokens

def create_initial_world_model():
    # Create an initial world model with basic information
    # You can customize this function based on the specific problem domain
    world_model = {
        "Geography": {"United States": {"number of states": 50, "largest state": "Alaska"}}
    }
    return world_model

def execute_python(code: str):
    # ret is defined in the code string

    loc = {}
    try:
        exec(code, globals(), loc)
    except Exception as e:
        print(f"Exception executing code {code}, {e}")
        return

    return loc['ret']

def prune_gene_results(res):
    pass

def validate_llm_response(goal, response):
    validation_prompt = f"I gave an LLM this goal: '{goal}' and it gave this response: '{response}'. Is this reasonable, or did something go wrong? [yes|no]"
    validation_response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=validation_prompt,
        temperature=0.0
    ).choices[0].text.strip()

    if validation_response.lower() == "yes":
        return True
    else:
        return False


def generate_tool_prompt(task):
    if 'PubChem' in task:
        api_name = 'PubChem'
    elif 'MYGENE' in task:
        api_name = 'mygene'
    elif 'PUBMED' in task:
        api_name = 'PubMed'
    else:
        print(f'Error. Tool not found in task: {task}')
        return None
    
    api_info = api_info_mapping[api_name]

    prompt = f"""You have access to query the {api_name} API. If a task starts with '{api_name}:' then you should create the code to query the {api_name} API based off the documentation and return the code to complete your task. If you use the {api_name} API, do not answer with words, simply answer with the code to query the API and then cease output. Be sure that it is a valid API call that will execute in a python interpreter
---
Here is the {api_name} documentation
{api_info}
---

        """.strip()
    
    return prompt

def get_ada_embedding(text):
    text = text.replace("\n", " ")
    return openai.Embedding.create(input=[text], model="text-embedding-ada-002")["data"][0]["embedding"]

def get_relevant(data, pinecone_index, num_relevant=5):
    query_embedding = get_ada_embedding(data)
    results = pinecone_index.query(query_embedding, top_k=num_relevant, include_metadata=True)
    sorted_results = sorted(results.matches, key=lambda x: x.score, reverse=True)
    return [str(item['metadata']["Result"]) for item in sorted_results]

def insert_doc_pinecone(index, doc, task_index, metadata):
    index.upsert([(task_index, doc, metadata)])

def insert_doc_llama_index(index, doc, task_index):
    doc = Document(doc, doc_id=task_index)
    index.insert(doc)

def query_knowledge_base(llama_index, query="Create an executive summary of the information. Do not include any further instruction.", response_mode="tree_summarize"):

    query_response = llama_index.query(query, response_mode=response_mode)
    return query_response.response

def parser(instruction, content):
    return openai.Completion.create(
        engine="text-davinci-003",
        prompt=instruction + '\nHere is the content to parse:\n' + content,
        temperature=0.0
    ).choices[0].text.strip()

@backoff.on_exception(partial(backoff.expo,max_value=50), (openai.error.RateLimitError, openai.error.APIError))
def get_gpt_completion(
        prompt,
        temp=0.0,
        engine="text-davinci-003",
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0
    ):
    response = openai.Completion.create(
        engine=engine,
        prompt=prompt,
        temperature=temp,
        max_tokens=get_max_completion_len(prompt),
        top_p=top_p,
        frequency_penalty=frequency_penalty,
        presence_penalty=presence_penalty
    )
    return response.choices[0].text.strip()
    
@backoff.on_exception(partial(backoff.expo,max_value=50), (openai.error.RateLimitError, openai.error.APIError))
def get_gpt_chat_completion(system_prompt, user_prompt, model="gpt-3.5-turbo", temp=0.0):
    response = openai.ChatCompletion.create(
        model=model, 
        messages=[{"role": "system", "content": system_prompt},
                  {"role": "user", "content": user_prompt}],
        temperature=temp
        )
    return response.choices[0]["message"]['content'].strip()











# Deprecated. Maybe will use in the future. storing here for now
def facilitator_agent(task:str, python: bool, result: str) -> str:
    if python:
        result = execute_python(result)

    prompt = f"""You are an AI who takes a world model, a task description, and the result of that task. You must integrate the result of that task into the world model. 
Do not delete any information from the world model, just integrate the results into it. Respond with an updated world model. The updated world model should be valid json.
Current world model: {world_model}
Task: {task}
Task result: {result}
Updated world model:"""
    response = openai.Completion.create(
        engine="text-davinci-003",
        prompt=prompt,
        temperature=0.1,
        max_tokens=get_max_completion_len(prompt),
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0
    )
    return literal_eval(response.choices[0].text.strip().replace('\n',''))