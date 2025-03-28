from abc import ABCMeta, ABC, abstractmethod, abstractclassmethod, abstractstaticmethod

from copy import deepcopy
from core.openai_tools import OPENAI_DEFAULT_MODEL
import openai
import tiktoken
import time
import json
from core.extensions import stream_text
from nice_functions import pprint, bold, green, blue, red, time_it, time_it_r
from core.tutor.systemmsg import (
    cqn_system_message,
    default_system_message,
    interpreter_system_message,
)
from core.tutor.utils import (
    remove_score_and_doc_from_valid_docs,
    yield_docs_and_first_sentence_if_tutor_id_not_apologizing,
    yield_docs,
)
import google.generativeai as genai
from core.data import DataBase
from core.data.parsing.papers.json_papers import JSONPaperParser


class Tutor(ABC):
    """
    Tutor class

    Args:
        embedding_db (VectorDatabase): the db with any or no source loaded
        embedding_db_name (str): Description of embedding_db.
        system_message (str)

    Return:
        Tutor object with no collections to load from. Use add_collection to add
        collection to load from.
    """

    def __init__(
        self,
        embedding_db,
        embedding_db_name="CQN database",
        system_message=default_system_message,
        engineer_prompts=True,
    ):
        """
        Args:
            - `embedding_db (VectorDatabase)`: the db with any or no source loaded
            - `embedding_db_name (str)`: Description of embedding_db.
            - `system_message (str)`
            - `engineer_prompts (bool)`: weather the chattutor bot should pull the full context from the last user message before querying.
            If true, the answering is slower but it is less likely to error, and it has more context so the answers are
            clearer and more correct. Defaults to True.

        Return:
            Tutor object with empty collection set. Use add_collection to add
            collection to load from.
        """
        self.embedding_db = embedding_db
        self.embedding_db_name = embedding_db_name
        self.collections = {}
        self.system_message = system_message
        self.engineer_prompts = engineer_prompts
        print(f"gemini_models = {[m for m in genai.list_models()]}")

    def add_collection(self, name, desc):
        """Adds a collection to self.collections
        Args:
            name (str): name of the collection to load form the chromadb (embedding_db)
            desc (str): description prompted to the model
        """
        self.collections[name] = desc

    def engineer_prompt(self, conversation, truncating_at=10, context=True):
        """
        Args:
            conversation: current conversation
            truncating_at: lookback for context (# of messages)
            context: if False, return last message otherwise engineer the prompt to have full context
        """
        # TODO: room for improvement.
        # context on pronouns:  for example who is "he/she/them/it" when refering to a paper/person
        if not context:
            return conversation[-1]["content"], False, False, ""
        truncated_convo = [
            f"\n\n{c['role']}: {c['content']}" for c in conversation[-truncating_at:][:-1]
        ]
        # todo: fix prompt to take context from all messages
        prompt = conversation[-1]["content"]
        print("entering engineer_prompt")
        # pprint("truncated_convo", truncated_convo)

        is_generic_message = "NO"  # currenlty not used
        # is_generic_message = time_it(self.simple_gpt, "is_generic_message")(
        #     f"""
        #     You are a model that detects weather a user given message is or isn't a generic message (a greeting or thanks of anything like that).
        #     Respond ONLY with YES or NO.
        #         - YES if the message is a generic message (a greeting or thanks of anything like that)
        #         - NO if the message asks something about a topic, person, scientist, or asks for further explanations on concepts that were discussed above.

        #     The current conversation between the user and the bot is:

        #     {truncated_convo}
        #     """,
        #     f"If the usere were to ask this: '{prompt}', would you clasify it as a message that refers to above messages from context? Respond only with YES or NO!",
        # )
        is_furthering_message = time_it(self.simple_gpt, "is_furthering_message")(
            f"""
            You are a model that detects weather a user given message refers to above messages and takes context from them, either by asking about further explanations on a topic discussed previously, or on a topic you just provided answer to. 
            Respond ONLY with YES or NO.
                - YES if the the user asks for an equation, or a summary, or more information, and he does not say where to get it from, but a paper was mentioned before.
                - YES if the user provided message is a message that refers to above messages from context (ie, uses the word 'it' to referes to a previus paper), or if the user refers with pronouns about people mentioned in the above messages, or if the user thanks you for a given information or asks more about it, or invalidates or validates a piece of information you provided.
                - NO if the message is a standalone message
            
            The current conversation between the user and the bot is:
            
            {truncated_convo}
            """,
            f"If the usere were to ask this: '{prompt}', would you clasify it as a message that refers to above messages from context? Respond only with YES or NO!",
        )
        pprint("truncated_convo", truncated_convo)
        get_furthering_message = "NO"
        is_generic_message = is_generic_message.strip() == "YES"
        is_furthering_message = is_furthering_message.strip() == "YES"

        pprint("is_generic_message", is_generic_message)
        pprint("is_furthering_message", is_furthering_message)
        pprint("get_furthering_message", get_furthering_message)

        if is_furthering_message:
            pprint("getting contex...")
            get_furthering_message = time_it(self.simple_gpt, "get_furthering_message")(
                f"""
                You are a model that detects weather a user given message refers to above messages and takes context from them, either by asking about further explanations on a topic discussed previously, or on a topic
                you just provided answer to. You will ONLY respond with:
                    - YES + a small summary of what the user message is refering to, the person the user is refering to if applicable, or the piece of information the user is refering to, if the user provided message is a message that refers to above messages from context, or if the user refers with pronouns about people mentioned in the above messages,
                    or if the user thanks you for a given information or asks more about it, or invalidates or validates a piece of information you provided . You must attach a small summary of what the user message is refering to,
                    but you still have to maintain the user's question and intention. The summary should be rephrased from the view point of the user, as if the user formulated the question to convey the context the user is refering to. This is really important!
                
                The current conversation between the user and the bot is:
                
                {truncated_convo}
                """,
                f"If the usere were to ask this: '{prompt}', would you clasify it as a message that refers to above messages from context? If YES, provide a small summary of what the user would refer to.",
            )
        if not is_furthering_message:
            get_furthering_message = "NO"
        if is_furthering_message:
            prompt += f"\n({get_furthering_message[4:]})"

        pprint("engineered prompt", green(prompt))

        return prompt, is_generic_message, is_furthering_message, get_furthering_message

    @abstractmethod
    def process_prompt(
        self, conversation, from_doc=None, threshold=0.5, limit=3, pipeline="openai"
    ):
        """Abstract function that should
        1. Engineer the prompt based on context (last few messages).
        2. return the most valid (closest to the question)
        documents from the embedding database.

        Args:
            conversation (list[{"role": str, "content":str}]): conversation
            from_doc (str | list[str], optional): doc(s) to pull from. Defaults to None.
            threshold (float, optional): Maximum distance from the query. Defaults to 0.5.
            limit (int, optional): Maximum documents to be returned. Defaults to 3.

        Advised Return:
            ```
            tuple[list[{"role": str, "content":str}], list[{
                "coll_desc": str, # collection description or empty
                "coll_name": str, # collection name,
                "doc": doc , # document returned
                "metadata": meta, # metadata
                "distance": float, # distance from the query
            }]]
            ```
            : a tuple containing the messages arraywith the latest message
            modified to fit the context, and the valid documents (closest to the query)
            that will be used as knowledge base by the tutor
        """
        pass

    def ask_question(
        self,
        conversation,
        from_doc=None,
        selectedModel=OPENAI_DEFAULT_MODEL,
        threshold=0.5,
        limit=3,
        pipeline="openai",
    ):
        """Function that responds to an asked question based
        on the current database and the loaded collections from the database

        Makes use of the abstract `process_prompt` function.

        Args:
            conversation : List({role: ... , content: ...})
            from_doc (Doc, optional): Defaults to None.
            selectedModel (str) : model to use,
            threshold (float) : maximum distance (distance = 1/ similarity)
            limit (int) : maximum documents that can be used from the knowledge base

        Yields:
            - response: str # text chunks that look like this:

            ```
                "data: {time: int, message: Message.json}}"
            ```
            where `Message` looks like this
            ```
            {
                "content" : str, # chunk content (part of the response)
                "valid_docs" : Oprional[list], # documents the query used to gain information
                # yielded only once at the begining
                "elapsed_time" : seconds, # time taken to generate the first response chunk
                "processing_prompt_time" : seconds # time taken to process prompt (gain context/use knowledge base etc.)
            }
            ```
        """

        st = time.time()
        messages, valid_docs = self.process_prompt(
            conversation, from_doc, threshold, limit, pipeline=pipeline
        )

        query = "NONE"
        if not isinstance(messages, list):
            query = messages["query"]
            messages = messages["messages"]
        en = time.time()
        processing_prompt_time = en - st

        # query_text = "NONE"
        # sql_query_data = None
        # if query != "NONE" and from_doc == None:
        #     sql_query_data, s = DataBase().safe_exec(query=query)
        #     if s == False or sql_query_data == []:
        #         query = "NONE"
        #         query_text = "NONE"
        #     else:
        #         query_text = f"IF THE USER IS ASKING ABOUT AUTHORS, IDS, OR PAPER TITLES, OR PAPERS OF AUTHORS, OR AUTHORS OF PAPERS, OR LISTINGS OF THE DB, USE ONLY THE INFORMATION THAT WAS PROVIDED TO YOU BELOW IN THE CQN DIRECT QUERY!! If the user isn't asking about a document's content or a broad topic, or related papers etc, on query, ignore the data above, and Provide this data exactly, in markdown form, stating that it is from the CQN DB:[{sql_query_data}].  This is the only info you will provide in this message about CQN DB. If paper ids are present above, also provide them as well! As well as links to arxiv or scholar of the paper, and of the author if present. DO NOT PROVIDE ANY OTHER INFORMATION YOU MIGHT KNOW OUTSIDE THIS INFO AND CQN INFO UNLESS EXPLICITLY ASKED SO BY THE USER!"

        # if from_doc != None:
        #     query_text = "IF YOU CAN USE THE RELEVANT SECTIONS ABOVE TO ANSWER QUESTIONS THE USER ASKS ABOUT THE PAPER, PLEASE QUOTE THE PART OF THE DOCUMENT YOU GOT YOUR INFO FROM. DO NOT COPY-PASTE THE WHOLE DOCUMENTS. OTHERWISE STATE THAT IT'S GENERAL KNOWLEDGE/WELL KNOWN, IF THE INFORMATION IS NOT FROM THE ABOVE DOCUMENTS/PAPERS. IF THE INFORMATION ASKED BY THE USER IS NOT STATED IN THE ABOVE DOCUMENTS, FEEL FREE TO USE YOUR OWN KNOWLEDGE, HOWEVER STATE THAT YOU DID SO, AND THAT YOU CAN'T FIND THE ANSWER IN THE PAPER, NEVERTHELESS ANSWER THE QUESTION, AND STATE THAT IF THE USER WANTS TO SEARCH FOR THIS TOPIC IN THE PAPER HE SHOULD BE MORE PRECISE WITH HIS QUERY. DO NOT LET THE USER WITHOUT AN ANSWER! DO NOT LET THE USER WITH NO ANSWER! HELP THE USER FIND THE ANSWER TO HIS/HER QUESTION!!! "

        # pprint(red("SQL_QUERY\n\n"), green(sql_query_data))

        # print("\n\n\n----------\n")
        # pprint("VALID_DOCS:\n", red(valid_docs))

        # print("\n----------\n\n\n")
        # print(green(messages[0]["content"]))

        # print(red(query_text))
        try:
            response, elapsed_time = [], 0.0
            if pipeline == "gemini":
                response = self.chat.send_message(
                    [
                        # these are the valid docs
                        messages[0]["content"],
                        "Use the data above to answer this question: " + messages[-1]["content"],
                    ],
                    stream=True,
                )
                elapsed_time = 0.0
            else:
                msgs = messages
                # for i in range(len(msgs)):
                #     msgs[i]["content"] = msgs[i]["content"][:12000]
                response, elapsed_time = time_it_r(openai.ChatCompletion.create)(
                    model=selectedModel,
                    messages=msgs,
                    temperature=0.7,
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                    stream=True,
                )

            # first_sentence = rf"({required_level_of_information}) "
            first_sentence = ""
            first_sentence_processed = False

            valid_docs = valid_docs[0:limit]
            valid_docs = remove_score_and_doc_from_valid_docs(valid_docs)
            pprint(red("VALID DOCS: "))
            print("\n")
            pprint(green(valid_docs))
            print("\n\n\n")
            for chunk in response:
                # print(chunk)
                if pipeline == "gemini":
                    try:
                        chunk = {"choices": [{"delta": {"content": chunk.text}}]}
                    except:
                        chunk = {"choices": [{"delta": {"content": "~"}}]}
                # print(chunk)

                # cache first setences to process it content and decide later on if we send or not documents
                # print(first_sentence)
                # print(len(first_sentence))
                # # ifx
                # print("yielding")
                # print(first_sentence)
                # print(len(first_sentence))

                # process first sentence
                if not first_sentence_processed:
                    first_sentence_processed = True
                    first_sentence += chunk["choices"][0]["delta"]["content"]
                    print("first_sentence", green(first_sentence))
                    for yielded_chain in yield_docs(valid_docs):
                        yielded_chain["elapsed_time"] = elapsed_time
                        yielded_chain["processing_prompt_time"] = processing_prompt_time
                        pprint(red("\n\tCHAIN:\n"))
                        pprint(green(yielded_chain))
                        yield yielded_chain

                # print("yielded\n")

                yield chunk["choices"][0]["delta"]
        except Exception as e:
            import logging

            logging.error("Error at %s", "division", exc_info=e)
            yield {"content": "", "valid_docs": []}
            # An error occured
            yield {
                "content": """\n\nSorry, I am not able to provide a response. 
                                
                                One of three things happened:
                                    - The context you provided was too wide, try to be more concise.
                                    - The files you uploaded were too large
                                    - I got disconnected from the server or I am currently being updated
                                """,
                "error": "true",
            }

    def count_tokens(self, string: str, encoding_name="cl100k_base") -> int:
        """Counting the number of tokens in a string using the specified encoding

        Args:
            string (str):
            encoding_name (str, optional): Defaults to 'cl100k_base'.

        Returns:
            int: number of tokens
        """
        encoding = tiktoken.get_encoding(encoding_name)
        num_tokens = len(encoding.encode(string))
        return num_tokens

    def truncate_conversation(self, conversation, token_limit=10000):
        """Truncates the conversation to fit within the token limit

        Args:
            conversation (List({role: ... , content: ...})): the conversation with the bot
            token_limit (int, optional): Defaults to 10000.

        Returns:
            List({role: ... , content: ...}): the truncated conversation
        """
        tokens = 0
        for i in range(len(conversation) - 1, -1, -1):
            tokens += self.count_tokens(conversation[i]["content"])
            if tokens > token_limit:
                print("reached token limit at index", i)
                return conversation[i + 1 :]
        pprint("total tokens in conversation (does not include system role):", tokens)
        return conversation

    def simple_gpt(
        self,
        system_message,
        user_message,
        models_to_try=[OPENAI_DEFAULT_MODEL],
        temperature=1,
    ):
        """Getting model's response for a simple conversation consisting of a system message and a user message

        Args:
            system_message (str)
            user_message (str)

        Returns:
            string : the first choice of response of the model
        """
        print("Model to try:\n")
        print(models_to_try)
        for model_to_try in models_to_try:
            try:
                response = openai.ChatCompletion.create(
                    model=model_to_try,
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=temperature,
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                    # stream=True,
                )
                return response.choices[0].message.content
            except Exception as e:
                print(red(model_to_try), "FAILED!")
                if model_to_try == models_to_try[-1]:
                    raise (e)

    def conversation_gpt(self, system_message, conversation):
        """Getting model's response for a conversation with multiple messages

        Args:
            system_message (str)
            conversation (List({role: ... , content: ...}))

        Returns:
            string : the first choice of response of the model
        """
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo-16k",
            messages=[{"role": "system", "content": system_message}] + conversation,
            temperature=1,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            stream=True,
        )
        return response.choices[0].message.content

    def stream_response_generator(
        self,
        conversation,
        from_doc: list[str] | None,
        selectedModel="gpt-3.5-turbo-16k",
        pipeline="openai",
    ):
        """Returns the generator that generates the response stream of ChatTutor.

        Args:
            conversation (List({role: str , content: str})): the current conversation
            from_doc (str): specify document if necesary, otherwise set to None
        """

        def generate():
            # This function generates responses to the questions in real-time and yields the response
            # along with the time taken to generate it.
            chunks = ""
            start_time = time.time()
            resp = self.ask_question(conversation, from_doc, selectedModel, pipeline=pipeline)
            for chunk in resp:
                chunk_content = ""
                if "content" in chunk:
                    chunk_content = chunk["content"]
                chunks += chunk_content
                chunk_time = time.time() - start_time
                # print(f"data: {json.dumps({'time': chunk_time, 'message': chunk})}\n\n")
                yield f"data: {json.dumps({'time': chunk_time, 'message': chunk})}\n\n"

        return generate
