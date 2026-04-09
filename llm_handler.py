import json
import os
from functools import wraps
from groq import Groq
from pydantic import ValidationError
import chromadb
from chromadb.utils import embedding_functions
import time
import re
import requests
from streamlit.runtime.scriptrunner import get_script_run_ctx
import streamlit as st

def token_tracker(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        print(f"[Process] '{func.__name__}' executes...")
        start_time = time.time()

        result, usage = func(*args, **kwargs)

        end_time = time.time()
        if usage:
            print("\n" + "-"*45)
            print(f"Token Report['{func.__name__}']")
            print(f"SUM: {usage.get('total_tokens', 0)} token | Time: {end_time - start_time:.2f} second(s)")
            print(f"Prompt: {usage.get('prompt_tokens', 0)} | Completion: {usage.get('completion_tokens', 0)}")

            try:
                if get_script_run_ctx():
                    if "token_tracker" not in st.session_state:
                        st.session_state.token_tracker = {}

                    tracker_key = func.__name__
                    if tracker_key == "evaluate_answer_and_decide":
                        current_step = st.session_state.get("theory_step", "unknown")
                        if current_step == "db_question":
                            tracker_key = "evaluate_theory_answer"
                        elif current_step == "github_questions":
                            tracker_key = "evaluate_github_answer"
                    if tracker_key not in st.session_state.token_tracker:
                        st.session_state.token_tracker[tracker_key] = {"prompt": 0, "completion": 0, "total": 0}
                        
                    st.session_state.token_tracker[tracker_key]["prompt"] += usage.get("prompt_tokens", 0)
                    st.session_state.token_tracker[tracker_key]["completion"] += usage.get("completion_tokens", 0)
                    st.session_state.token_tracker[tracker_key]["total"] += usage.get("total_tokens", 0)
                    
            except Exception as e:
                print(f" Token Save Error: {e}")
        return result, usage
    return wrapper


class LLMHandler:
    def __init__(self):
        #Groq Connection
        self.api_key = os.environ.get("GROQ_API_KEY")
        self.client = Groq(api_key=self.api_key)

        #VectorDB Conenction
        self.db_path = os.path.join(os.getcwd(), "question-db")
        self.client_db = chromadb.PersistentClient(path = self.db_path)
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")

        #Collection
        self.collection = self.client_db.get_collection(name = "interview_questions", embedding_function = self.embedding_fn)

    def local_security_guardrail(self, user_input: str) -> dict:
        user_input_lower = user_input.lower()
        injection_patterns = [
            r"ignore\s+(all\s+)?previous\s+instructions?", 
            r"forget\s+(all\s+)?previous",                 
            r"what\s+is\s+your\s+system\s+prompt",         
            r"print\s+(your\s+)?instructions",             
            r"give\s+me\s+(a\s+)?(10|ten|full\s+score)",   
            r"just\s+pass\s+me",                           
            r"you\s+are\s+now",                            
            r"bypass\s+rules?",                            
            r"ignore\s+(the\s+)?rules?",                   
            r"new\s+instructions?",                        
            r"jailbreak"
        ]
        if any(re.search(pattern, user_input_lower) for pattern in injection_patterns):
            return {"status": "unsafe", "reason": "System manipulation or prompt injection attempt detected."}
        return {"status": "safe", "reason": ""}


    def _call_llm(self, messages, response_format=None, model="llama-3.1-8b-instant", temperature=0.1, provider="groq"):
            print(f" LLM Routing: Trying {model} via {provider}...")
            try:
                if provider == "groq":
                    completion = self.client.chat.completions.create(
                        model=model,
                        messages=messages,
                        response_format=response_format,
                        temperature=temperature
                    )
                    standard_usage = {
                        "prompt_tokens": completion.usage.prompt_tokens,
                        "completion_tokens": completion.usage.completion_tokens,
                        "total_tokens": completion.usage.total_tokens
                    }

                    return completion.choices[0].message.content, standard_usage
            except Exception as e:
                print(f"Model Error ({model}): {e}")
                return None, None
            
    @token_tracker  
    def parse_cv_with_llm_and_validation(self, cv_text: str, model_class, max_retries: int = 2):
        schema_str = json.dumps(model_class.model_json_schema(), indent=2)

        system_prompt = f"""
        You are an ELITE Technical Recruiter and Data Extractor. 
        Your primary mission is to identify if the provided text is a professional CV or Resume.

        STRICT INSTRUCTIONS:
        1. If the text is a Cover Letter, Job Description, or unrelated random text, you MUST set "is_cv": false and leave all other lists (projects, technical_skills, etc.) EMPTY.
        2. Do NOT extract data from Cover Letters. A Cover Letter is a narrative letter, not a structured CV.
        3. If "is_cv" is true, extract the data strictly following the schema. 
        4. Do NOT hallucinate. If a field is not explicitly or strongly implied in the CV, use the default values.
        5. Return ONLY a valid JSON object.
    
        JSON SCHEMA:
        {schema_str}
        """
    
        error_feedback = ""

        for attempt in range(max_retries):
            print(f"Attempting LLM Parsing... (attempt: {attempt + 1}/{max_retries})")
        
            user_prompt = f"Extract structured data from this CV Text:\n{cv_text}\n"
            if error_feedback:
                user_prompt += f"\nWARNING: Your previous response failed validation. Please fix this exact error: {error_feedback}"

            try: 
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
                
                raw_response, usage = self._call_llm(
                    messages = messages,
                    response_format={"type": "json_object"},
                    temperature = 0.1
                )
                
                raw_json = json.loads(raw_response)
                print(f"RAW LLM RESPONSE: {raw_response}") 
                validated_data = model_class(**raw_json)
                print(f"Validated LLM RESPONSE: {validated_data}") 

                print("Pydantic Validation Passed! Output perfectly matches the schema.")
                return validated_data.model_dump(), usage
    
            except json.JSONDecodeError:
                error_feedback = "You did not return a valid JSON string. Ensure there is no trailing text or markdown."
                print("JSON Format Error. Retrying...")
            
            except ValidationError as e:
                error_feedback = f"Schema validation failed. Check your keys and data types:\n{e}"
                print("Pydantic Schema Error! LLM deviated from the schema. Retrying...")
            
            except Exception as e:
                print(f"Unexpected API error: {e}")
                break

        print(" Max retries reached. Parsing failed to produce valid data.")
        return None, None
    
    def fetch_question_from_db(self, tech_stack: list, n_results: int = 1):
        if not tech_stack or len(tech_stack) == 0:
            print("Empty tech stack, request manuel input from the user...")
            return {
                "status": "missing_data",
                "error": "please enter your tech stacks (with ,)"
            }
        search_query = ", ".join(tech_stack)
        print(f"[DB] looking for questions for theese techstack: {search_query}")

        try:
            results = self.collection.query(
                query_texts = [search_query],
                n_results = n_results
            )

            if not results['documents'] or not len(results['documents'][0]) > 0:
                print("No matching question")
                return None
            
            questions_list = []
            for i in range(len(results['documents'][0])):
                questions_list.append({
                    "question": results['documents'][0][i],
                    "metadata": results['metadatas'][0][i] if results.get("metadatas") else {}
                })
                
            print(f"[DB] {len(questions_list)} Question(s) pulled successfully")
            return questions_list
    
        except Exception as e:
            print(f"[DB] Error during pulling question: {e}")
            return None
    
    @token_tracker
    def evaluate_answer_and_decide(self, question: str, candidate_answer: str, ideal_criteria: dict,  llm_model, provider, conversation_history: str = ""):
        system_prompt = """
       You are an expert, empathetic Senior Engineering Manager conducting a technical interview.
        Evaluate the candidate's answer and decide the exact next step based on the rules below.
        
        DECISION CRITERIA (How to choose the decision):
        - NEXT_QUESTION: Choose this ONLY if the answer is completely correct and covers all ideal criteria (Score 9-10).
        - DEEP_DIVE: Choose this if the answer is mostly correct but surface-level, or missing some advanced aspects (Score 6-8).
        - BRIDGE: Choose this if the answer is fundamentally wrong, highly incomplete, or totally off-track (Score 1-5).
        
        RULES FOR BOT SPEECH (Speak directly to the candidate in English):
        - If NEXT_QUESTION: Praise their complete understanding, confirm the answer is spot on, and state you are ready to move to the next topic.
        - If DEEP_DIVE: Validate the parts they got right. Then, say something like "That's correct, but there is also another aspect to this..." and ask a specific follow-up question to dig into the missing detail.
        - If BRIDGE: Empathize with their logic ("I see why you'd think that..."). Do NOT just give them the correct answer. Instead, give them a technical hint or clue about the correct approach, and ask them how they would solve it using this new hint. Wait for their answer.
        
        OUTPUT FORMAT (Strict JSON):
        {
            "score": <int 1-10>,
            "decision": "<DEEP_DIVE | BRIDGE | NEXT_QUESTION>",
            "bot_speech": "<The exact words you will say to the candidate>",
            "internal_notes": "<Brief note on what they missed or nailed>"
        }
        """
        user_prompt = "INTERVIEW DATA:\n"
        if conversation_history:
            user_prompt += f"Previous Chat History for this topic:\n{conversation_history}\n\n"
        user_prompt += f"Original Question: {question}\n"
        user_prompt += f"Ideal Criteria: {ideal_criteria}\n"
        user_prompt += f"Candidate's Latest Answer: {candidate_answer}\n"
        
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            raw_response, usage = self._call_llm(
                messages = messages,
                response_format = {"type": "json_object"},
                model = llm_model,
                provider = provider,
                temperature = 0.3
            )

            result_json = json.loads(raw_response)
            return result_json, usage
        except Exception as e:
            print(f"[LLMHandler] Evaluation Error: {e}")
            return {"decision": "ERROR", "error_message": str(e)}, None
    
    @token_tracker
    def generate_github_questions(self, github_data: dict, llm_model: str, provider: str):
        from cv_parser import _clean_readme
        system_prompt = """
        You are an elite, highly technical Senior Software Architect conducting an interview.
        Analyze the candidate's actual GitHub repository data (Dependencies, README snippet, and Code Skeleton).
        
        YOUR TASK:
        Create exactly 3 highly specific, challenging interview questions based ONLY on the provided GitHub data.
        
        The 3 questions MUST cover different technical aspects:
        1. Architecture / System Design (e.g., Why this framework? How do these classes interact?)
        2. Core Logic / State Management (e.g., How does a specific method handle data flow?)
        3. Edge Cases / Security / Error Handling (e.g., What happens if a specific dependency fails?)
        
        RULES:
        - Speak directly to the candidate in English.
        - Reference exact class names, method names, or specific dependencies from their data.
        - Do NOT ask generic questions. They must be tailored strictly to their codebase.
        
        OUTPUT FORMAT (Strict JSON):
        {
            "questions": [
                {
                    "topic": "<Architecture | Core Logic | Edge Cases>",
                    "question_text": "<The exact words you will say to the candidate>",
                    "expected_focus": "<Brief internal note on what technical detail you expect in a good answer>"
                },
                ...
                // exactly 3 items
            ]
        }
        """
        github_context_str = ""
    
        if isinstance(github_data, dict):
            github_data = [github_data]
        
        repo_count = len(github_data)
        readme_limit = 5000 if repo_count == 1 else 2500
        skeleton_limit = 8000 if repo_count == 1 else 3500

        for idx, repo in enumerate(github_data):
            repo_name = repo.get("name", f"Repo_{idx+1}")
            deps = str(repo.get('dependencies', 'None'))            
            raw_readme = str(repo.get('readme', 'No README'))

            cleaned_readme = _clean_readme(raw_readme)
            final_readme = cleaned_readme[:readme_limit] + ("..." if len(cleaned_readme) > readme_limit else "")
        
            skeleton_raw = str(repo.get('skeleton', 'No Skeleton'))
            final_skeleton = skeleton_raw[:skeleton_limit] + ("..." if len(skeleton_raw) > skeleton_limit else "")

            github_context_str += f"\n--- REPOSITORY: {repo_name} ---\n"
            github_context_str += f"DEPENDENCIES:\n{deps}\n"
            github_context_str += f"README:\n{final_readme}\n"
            github_context_str += f"SKELETON:\n{final_skeleton}\n"

        user_prompt = f"""
        CANDIDATE'S GITHUB DATA ({repo_count} Repositories found):
        {github_context_str}
        """

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            raw_response, usage = self._call_llm(
                messages=messages, 
                response_format={"type": "json_object"}, 
                model=llm_model,
                provider=provider,
                temperature=0.4 
            )

            result_json = json.loads(raw_response)
            return result_json, usage
        except Exception as e:
            print(f"[LLMHandler] GitHub Question Generation Error: {e}")
            return {"decision": "ERROR", "error_message": str(e)}, None
    
    def fetch_live_coding_task(self, language: str, difficulty: str):
        try:
            results = self.collection.get(
                where = {
                    "$and": [
                        {"type": "live_coding"},
                        {"language": language},
                        {"difficulty": difficulty}
                    ]
                }
            )
            if not results['metadatas'] or len(results['metadatas']) == 0:
                print("[DB] No matching live coding exercise founded.")
                return None
            
            return results['metadatas'][0] # random da olabilir
        except Exception as e:
            print(f"[DB] Error during pulling the task: {e}")
            return None
    
    def execute_code_sandbox(self, code: str, language: str) -> dict:
        print(f"[Sandbox] {language.capitalize()} code executeing via Wandbox...") 
        url = "https://wandbox.org/api/compile.json"

        compiler_map = {
            "python": "cpython-3.7.17", # Eğer bu hata verirse kendi bulduğun pypy-2.7-v7.3.17 yapıp geçebilirsin
            "cpp": "clang-14.0.6",
            "c": "clang-14.0.6-c",
            "java": "openjdk-jdk-21+35"
        }
        compiler = compiler_map.get(language.lower())

        payload = {
            "compiler": compiler,
            "code": code
        }
       
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.post(url, json=payload, headers=headers, timeout=15)

            if response.status_code == 200:
                data = response.json()

                stdout = data.get("program_message", "")
                stderr = data.get("compiler_error", "") or data.get("program_error", "")
                exit_code = int(data.get("status", "-1"))
                
                return {
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": exit_code
                }
            else:
                return {"stdout": "", "stderr": f"Cloud API ERROR: {response.status_code} - {response.text}", "exit_code": -1}         
        except requests.exceptions.Timeout:
            return {"stdout": "", "stderr": "Execution Timeout: Code took too long to run.", "exit_code": -1}
        except Exception as e:
            return {"stdout": "", "stderr": f"Cloud Connection Error: {e}", "exit_code": -1}


    @token_tracker
    def evaluate_live_coding(self, task_description: str, candidate_code: str, execution_log: dict, language: str, llm_model: str, provider: str, conversation_history: str = ""):
        
        system_prompt = f"""
        You are an elite Senior Staff Engineer evaluating a candidate's code during a live coding interview.
        You are NOT guessing if the code works. You are provided with the EXACT execution logs (stdout/stderr) from a secure sandbox.
        
        YOUR TASK:
        1. Review the Execution Logs: Base your feedback heavily on the provided STDOUT (prints/success) and STDERR (crashes/errors).
        2. Evaluate Code Quality: Check for Time/Space Complexity (Big O), edge case handling, and clean code principles.
        
        DECISION CRITERIA:
        - PASS: Execution has NO errors (stderr is empty), output is correct, and the code is optimal.
        - REFACTOR_REQUEST: The code works (no errors) but is inefficient (e.g., O(n^2) instead of O(n)), OR misses minor edge cases.
        - FAIL: The code crashed (stderr has errors), has infinite loops, or is fundamentally wrong.
        
        RULES FOR BOT SPEECH:
        - Speak directly to the candidate in English.
        - If they got an error (stderr): DO NOT provide the fixed code. Tell them the error type (e.g., "The sandbox threw an IndexError"), and ask how they would debug it.
        - If REFACTOR_REQUEST: Validate their working solution, but ask a follow-up question to optimize time complexity.
        - If PASS: Praise the efficiency and confirm the successful output. Let them know the technical assessment is complete.
        
        OUTPUT FORMAT (Strict JSON):
        {{
            "score": <int 1-10>,
            "decision": "<PASS | REFACTOR_REQUEST | FAIL>",
            "bot_speech": "<The exact words you will say to the candidate>",
            "complexity_notes": "<Internal notes on Time/Space complexity>",
            "bug_report": "<Internal notes on bugs based on the stderr>"
        }}
        """
        user_prompt = "LIVE CODING DATA:\n"
        if conversation_history:
            user_prompt += f"Previous Chat History:\n{conversation_history}\n\n"
            
        user_prompt += f"Task Description: {task_description}\n"
        user_prompt += f"Candidate's Code:\n```{language}\n{candidate_code}\n```\n"
        
        user_prompt += f"SANDBOX EXECUTION LOGS:\n"
        user_prompt += f"Standard Output (STDOUT):\n{execution_log.get('stdout', 'None')}\n"
        user_prompt += f"Standard Error (STDERR):\n{execution_log.get('stderr', 'None')}\n"
        user_prompt += f"Exit Code: {execution_log.get('exit_code', 'Unknown')}\n"

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]

            raw_response, usage = self._call_llm(
                messages=messages, 
                response_format={"type": "json_object"}, 
                model=llm_model,
                provider=provider,
                temperature=0.2 
            )

            return json.loads(raw_response), usage
        except Exception as e:
            print(f"[LLMHandler] Live Coding Evaluation Error: {e}")
            return {"decision": "ERROR", "error_message": str(e)}, None
