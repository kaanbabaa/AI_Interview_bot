import streamlit as st
from llm_handler import LLMHandler
from cv_parser import extract_text_from_pdf, CVProfile, analyze_github_links, fetch_user_repos, match_repos_locally, get_single_repo_info, fetch_repo_contents_smart
import os
import uuid
from streamlit_ace import st_ace
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
st.set_page_config(page_title = "AI Interview Platform", layout = "wide")

if "stage" not in st.session_state:
    st.session_state.stage = "setup"

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if "review_done" not in st.session_state:
    st.session_state.review_done = False

stages_map = {"setup": 0, "theory": 25, "github": 50, "live_coding": 75, "finished": 100}
st.progress(stages_map[st.session_state.stage], text=f"Interview Stage: {st.session_state.stage.upper()}")

#st.title("AI Senior Software Engineer")
st.markdown("---")

if st.session_state.stage == "setup":
    st.header("1. Stage: Profile Setup")
    st.info("Please upload your CV (PDF) so we can assess your skills.")

    uploadad_file = st.file_uploader("upload your CV", type = ["pdf"])

    if uploadad_file is not None:
        if "parsed_json" not in st.session_state or st.session_state.get("last_uploaded") != uploadad_file.name:
            with st.spinner("Extracting and analyzing your CV..."):
                unique_cv_id = uuid.uuid4().hex
                temp_pdf_path = f"team_cv_upload_{unique_cv_id}.pdf"

                with open(temp_pdf_path, "wb") as f:
                    f.write(uploadad_file.getbuffer())
                
                handler = LLMHandler()
                extracted_text = extract_text_from_pdf(temp_pdf_path)

                if os.path.exists(temp_pdf_path):
                    os.remove(temp_pdf_path)

                if extracted_text:
                    parsed_json, usage = handler.parse_cv_with_llm_and_validation(
                        cv_text=extracted_text, model_class=CVProfile,
                    )
                    if parsed_json:
                        #if not parsed_json.get("is_cv", False):
                         #   st.error(" This document is not a CV (detected as a Cover Letter or other text). Please upload a Resume.")
                          #  st.stop()
                        st.session_state.parsed_json = parsed_json
                        st.session_state.last_uploaded = uploadad_file.name

        if "parsed_json" in st.session_state:
            if not st.session_state.review_done:
                st.divider()
                st.header(" Step 1.1: Review & Edit Your Skills")
                st.info("We extracted these skills from your CV. Please add missing ones or remove incorrect ones.")

                extracted_skills = []
                for cat in st.session_state.parsed_json.get("technical_skills", []):
                    extracted_skills.extend(cat.get("skills", []))
                #daha fazla skill ekle ve common skillsi extracted skills ile intersectle sonra kesismeyenleri ekle sadece
                common_skills = ["Python", "JavaScript", "React", "Node.js", "Java", "C++", "Docker", "AWS", "SQL", "Git"]
                edited_skills = st.multiselect(
                    "Your Technical Stack:",
                    options=list(set(extracted_skills + common_skills)),
                    default=extracted_skills
                )

                if st.button("Save Skills & Proceed"):
                    if len(edited_skills) < 3:
                        st.warning("Please select at least 3 skills to proceed with a technical interview.")
                    else:
                        st.session_state.tech_stack = edited_skills
                        st.session_state.review_done = True
                        st.success("Skills saved! Proceeding to Architecture setup...")
                        st.rerun()
                st.stop()

            if st.session_state.review_done:
                found_repos, profile_username = analyze_github_links(st.session_state.parsed_json)
                target_repos = []
                added_repo_ids = set()

                if profile_username:
                    print(f"Profile founded: {profile_username}. Repos are going to be pulled from the API...")
                    all_repos = fetch_user_repos(profile_username)
                    if all_repos:
                        print("Local text matching process started...")
                        cv_projects = st.session_state.parsed_json.get("projects", [])
                        matched_repos = match_repos_locally(cv_projects, all_repos)

                        if matched_repos:
                            target_repos.extend(matched_repos)
                            for repo in matched_repos:
                                unique_id = f"{repo['username']}/{repo['repo']}"
                                added_repo_ids.add(unique_id)

                if found_repos:
                    print("Repos are founded in the CV Document!")
                    for repo in found_repos:
                        unique_id = f"{repo['username']}/{repo['repo']}"
                        if unique_id not in added_repo_ids:
                            print(f"New Repo added(direct link); {unique_id}")
                            repo_info = get_single_repo_info(repo['username'], repo['repo'])
                            if repo_info is not None:
                                target_repos.append({
                                    "username": repo["username"],
                                    "repo": repo["repo"],
                                    "def_branch": repo_info["default_branch"],
                                    "language": repo_info["language"],
                                    "cv_project": repo["repo"]
                                })
                            added_repo_ids.add(unique_id)

                if not target_repos:
                    print("No Valid GitHub link founded")
                    st.session_state.github_status = "not_found"
                    
                else:
                    print("Repos found and saved to memory!")
                    st.session_state.candidate_repos = target_repos
                    st.session_state.github_status = "found"

            if "github_status" in st.session_state:
                st.markdown("### Architecture / GitHub Review Setup")

                if st.session_state.github_status == "not_found":
                    st.warning("We couldn't automatically find any GitHub links in your CV.")
                    if "attempt_count" not in st.session_state:
                        st.session_state.attempt_count = 0
                    
                    skip_github = st.checkbox("I don't have a public GitHub repository (Skip Architecture Review)")
                    if skip_github:
                        st.info("NOTE: If you skip this, personalized architecture and repository-specific questions will be disabled.")
                        st.session_state.github_flag = False
                        st.session_state.selected_repos = []
                    else:
                        st.session_state.github_flag = True

                        if st.session_state.attempt_count < 3:
                            st.info(f"Please enter your GitHub Username. We will fetch your public repositories and match them with your projects.(Attempt {st.session_state.attempt_count + 1}/3)")
                            manual_username = st.text_input("GitHub Username:", key="manual_user")
                            search_clicked = st.button("Search Repositories")

                            if search_clicked and manual_username:
                                with st.spinner("Searching for user..."):
                                    all_repos = fetch_user_repos(manual_username)
                                    
                                    if all_repos:
                                        st.session_state.attempt_count = 0
                                        cv_projects = st.session_state.parsed_json.get("projects", [])
                                        matched_repos = match_repos_locally(cv_projects, all_repos)
                                        
                                        repo_options = [f"{manual_username}/{r['name']} (Branch: {r.get('default_branch', 'main')})" for r in all_repos]
                                        default_options = []

                                        if matched_repos:
                                            st.success(f"Great! We automatically matched {len(matched_repos)} repositories to your CV projects.")
                                            for mr in matched_repos:
                                                default_str = f"{manual_username}/{mr['repo']} (Branch: {mr.get('def_branch', 'main')})"
                                                if default_str in repo_options:
                                                    default_options.append(default_str)
                                        else:
                                            st.info("We couldn't automatically match your repos to your CV projects, but you can select them below.")
                                        choices = st.multiselect(
                                            "Select your repositories for the architecture review:", 
                                            options=repo_options,
                                            default=default_options 
                                        )
                                        selected_repos = []
                                        for choice in choices:
                                            idx = repo_options.index(choice)
                                            selected_repos.append({
                                                "username": manual_username,
                                                "repo": all_repos[idx]["name"],
                                                "def_branch": all_repos[idx].get("default_branch"),
                                                "language": all_repos[idx].get("language")
                                            })
                                
                                        st.session_state.selected_repos = selected_repos
                                    else:
                                        st.session_state.attempt_count += 1
                                        st.error(f"No public repositories found for this user.Remaining attempts: {3 - st.session_state.attempt_count}")
                                        st.rerun()
                        else:
                            st.error("We've tried 3 times and couldn't find the user. Please enter your repository links manually.")
                            repo_count = st.number_input("How many repositories do you want to add?", min_value=1, max_value=5, value=1)

                            manual_repo_urls = []
                            for i in range(repo_count):
                                url = st.text_input(f"GitHub Repository URL #{i+1}:", key=f"fail_fallback_repo_{i}")
                                if url:
                                    manual_repo_urls.append(url)
                            selected_repos = []
                            for manual_url in manual_repo_urls:
                                parts = manual_url.strip("/").split("/")
                                if len(parts) >= 2:
                                    u_name = parts[-2]
                                    r_name = parts[-1]
                                    repo_info = get_single_repo_info(u_name, r_name)

                                    if repo_info is not None:
                                        selected_repos.append({
                                            "username": u_name,
                                            "repo": r_name,
                                            "def_branch": repo_info["default_branch"],
                                            "language": repo_info["language"]
                                        })
                                    else:
                                        st.error(f"Cannot access repository: {u_name}/{r_name}")
                                else:
                                    st.error(f"Invalid URL format: {manual_url}")
                                
                            st.session_state.selected_repos = selected_repos
                
                else:
                    skip_github_found = st.checkbox("I don't want to include my GitHub repositories (Skip Architecture Review)")
                    if skip_github_found:
                        st.info("NOTE: If you skip this, personalized architecture and repository-specific questions will be disabled.")
                        st.session_state.github_flag = False
                        st.session_state.selected_repos = []
                    else:
                        st.session_state.github_flag = True
                        st.success("We matched some repositories from your CV!")

                        candidate_repos = st.session_state.get("candidate_repos", [])
                        options = [f"{r['username']}/{r['repo']} (Branch: {r['def_branch']})" for r in candidate_repos]

                        Other_option = ("Other (These are incorrect, let me enter manually)")
                        options.append(Other_option)

                        choice = st.multiselect("Select the repository(s) for the architecture review:", options)
                        selected_repos = []

                        for selected_item in choice:
                            if selected_item != Other_option:
                                idx = options.index(selected_item)
                                selected_repos.append(candidate_repos[idx])

                        if Other_option in choice:
                            st.warning("Please provide your correct repository links.")
                            repo_count = st.number_input("How many additional repositories do you want to add?", min_value=1, max_value=5, value=1)

                            manual_repo_urls = []
                            for i in range(repo_count):
                                url = st.text_input(f"GitHub Repository URL #{i+1}:", key=f"manual_repo_found_{i}")
                                if url:
                                    manual_repo_urls.append(url)

                            for manual_url in manual_repo_urls:
                                parts = manual_url.strip("/").split("/")
                                if len(parts) >= 2:
                                    u_name = parts[-2]
                                    r_name = parts[-1]
                                    repo_info = get_single_repo_info(u_name, r_name)

                                    if repo_info is not None:
                                        selected_repos.append({
                                            "username": u_name,
                                            "repo": r_name,
                                            "def_branch": repo_info["default_branch"],
                                            "language": repo_info["language"]
                                        })
                                    else:
                                        st.error(f"Cannot access repository: {u_name}/{r_name}. It might be private or invalid.")
                                else:
                                    st.error(f"Invalid URL format: {manual_url}")

                        st.session_state.selected_repos = selected_repos

                    st.markdown("---")
                    st.divider() 
                    st.subheader(" Final Review")
                    st.info("Please review the information the AI Interviewer will use. Once you start, the interview will be based strictly on this data.")

                col1, col2 = st.columns(2)

                with col1:
                    st.markdown("** Confirmed Tech Stack:**")
                    skills = st.session_state.get("tech_stack", [])
                    if not skills: 
                        for cat in st.session_state.parsed_json.get("technical_skills", []):
                            skills.extend(cat.get("skills", []))

                    if skills:
                        st.write(", ".join(skills))
                    else:
                        st.write("*No technical skills found.*")

                    st.markdown("** Extracted Projects:**")
                    projects = st.session_state.parsed_json.get("projects", [])
                    if projects:
                        for p in projects:
                            st.markdown(f"- **{p['name']}** *(Stack: {p.get('tech_stack', 'N/A')})*")
                    else:
                        st.write("*No projects extracted.*")

                with col2:
                    st.markdown("** Selected GitHub Repositories:**")
                    repos = st.session_state.get("selected_repos", [])
                    if st.session_state.get("github_flag", False) and repos:
                        for r in repos:
                            st.markdown(f"- `github.com/{r['username']}/{r['repo']}` *(Branch: {r['def_branch']})*")
                    else:
                        st.write("*No repositories selected or GitHub analysis skipped.*")

                st.markdown("<br>", unsafe_allow_html=True)
                if st.button(" Confirm Profile and Start Interview", use_container_width=True, type="primary"):
                    st.session_state.stage = "theory"
                    st.rerun()

elif st.session_state.stage == "theory":
    st.header(" Stage 2: Technical & Architecture Interview")

    handler = LLMHandler()

    if "theory_step" not in st.session_state:
        st.session_state.theory_step = "db_question"
    
    if "q_attempts" not in st.session_state:
        st.session_state.q_attempts = 0

    if "db_scores" not in st.session_state:
        st.session_state.db_scores = []
    if "github_scores" not in st.session_state:
        st.session_state.github_scores = []

    if st.session_state.theory_step == "db_question":
        if "db_questions_list" not in st.session_state:
            with st.spinner("Fetching a technical question from our database..."):
                db_res_list = handler.fetch_question_from_db(st.session_state.get("tech_stack", []), n_results=2)

                if db_res_list:
                    st.session_state.db_questions_list = db_res_list
                    st.session_state.db_q_idx = 0

                    first_q = st.session_state.db_questions_list[0]
                    msg = f"Let's start with a technical deep-dive. {first_q['question']}"
                    st.session_state.chat_history.append({"role": "assistant", "content": msg})
                    
                else:
                    st.error("Could not find a question in DB. Skipping to GitHub questions...")
                    st.session_state.theory_step = "github_questions"
                    st.rerun()
    
    elif st.session_state.theory_step == "github_questions":
        if not st.session_state.get("github_flag") or not st.session_state.get("selected_repos"):
            st.session_state.stage = "live_coding"
            st.rerun()
        if "github_questions_list" not in st.session_state:
            with st.spinner("Scanning your actual code, parsing skeletons, and crafting deep architecture questions..."):
                target_repos = st.session_state.get("selected_repos", [])
                final_pipeline_data = []

                for repo_info in target_repos:
                    u_name = repo_info["username"]
                    r_name = repo_info["repo"]
                    branch = repo_info["def_branch"]

                    repo_details = fetch_repo_contents_smart(u_name, r_name, branch)

                    repo_packet = {
                        "name": r_name, 
                        "dependencies": repo_details.get("dependencies", {}), 
                        "readme": repo_details.get("readme", ""), 
                        "skeleton": repo_details.get("source_codes", {}) 
                    }

                    final_pipeline_data.append(repo_packet)

                gh_res, usage = handler.generate_github_questions(
                    github_data=final_pipeline_data, llm_model= "llama-3.3-70b-versatile",
                    provider="groq",
                )

                if gh_res and "questions" in gh_res:
                    st.session_state.github_questions_list = gh_res["questions"]
                    st.session_state.github_q_idx = 0

                    first_gh_q = st.session_state.github_questions_list[0]
                    msg = f"Awesome, I've reviewed your repositories. Let's dive into your code. {first_gh_q['question_text']}"
                    st.session_state.chat_history.append({"role": "assistant", "content": msg})
                else:
                    st.error("Could not generate GitHub questions. Moving to live coding...")
                    st.session_state.stage = "live_coding"
                    st.rerun()

    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    if candidate_answer := st.chat_input("Enter your answer..."):
        st.session_state.q_attempts += 1
        st.session_state.chat_history.append({"role": "user", "content": candidate_answer})

        with st.chat_message("user"):
            st.markdown(candidate_answer)
        
        with st.chat_message("assistant"):
            with st.spinner("Interviewer is evaluating your answer..."):
                current_q = ""
                ideal_crit = ""

                if st.session_state.theory_step == "db_question":
                    current_q_data = st.session_state.db_questions_list[st.session_state.db_q_idx]
                    current_q = current_q_data['question']
                    ideal_crit = current_q_data['metadata'].get('ideal_criteria', '')
                elif st.session_state.theory_step == "github_questions":
                    current_q_data = st.session_state.github_questions_list[st.session_state.github_q_idx]
                    current_q = current_q_data['question_text']
                    ideal_crit = current_q_data['expected_focus']
                
                history_str = ""
                if st.session_state.q_attempts > 1:
                    relevant_history = st.session_state.chat_history[-(st.session_state.q_attempts * 2 - 1): -1]
                    history_str = "\n".join([f"{msg['role'].capitalize()}: {msg['content']}" for msg in relevant_history])
                
                eval_res, usage = handler.evaluate_answer_and_decide(
                    question=current_q,
                    candidate_answer=candidate_answer,
                    ideal_criteria=ideal_crit,
                    llm_model="llama-3.3-70b-versatile",
                    provider="groq",
                    conversation_history=history_str
                )

                decision = eval_res.get("decision", "ERROR")
                bot_speech = eval_res.get("bot_speech", "Sorry, I had a mental block. Could you repeat that?")
                score = eval_res.get("score", 5)

                if decision == "ERROR":
                    st.error("LLM Evaluation Failed! Please try submitting your answer again.")
                    st.session_state.q_attempts -= 1
                else:
                    if st.session_state.q_attempts == 1:
                        score_data = {"question": current_q, "scores": [score]}
                        if st.session_state.theory_step == "db_question":
                            st.session_state.db_scores.append(score_data)
                        else:
                            st.session_state.github_scores.append(score_data)
                    else:
                        target_list = st.session_state.db_scores if st.session_state.theory_step == "db_question" else st.session_state.github_scores
                        for item in target_list:
                            if item["question"] == current_q:
                                item["scores"].append(score) # Puanı listeye ekliyoruz, ortalamayı sonda alacağız
                                break
                
                    if st.session_state.q_attempts >= 2 and decision != "NEXT_QUESTION":
                        decision = "NEXT_QUESTION"
                        bot_speech = "I appreciate your reasoning. We have a lot of ground to cover, so let's leave this topic here and move on to the next one."

                    st.markdown(bot_speech)
                    st.session_state.chat_history.append({"role": "assistant", "content": bot_speech})

                    if decision == "NEXT_QUESTION":
                        if st.session_state.theory_step == "db_question":
                            st.session_state.db_q_idx += 1
                            st.session_state.q_attempts = 0

                            if st.session_state.db_q_idx < len(st.session_state.db_questions_list):
                                next_q = st.session_state.db_questions_list[st.session_state.db_q_idx]
                                next_msg = f"Alright, next topic: {next_q['question']}"
                                st.session_state.chat_history.append({"role": "assistant", "content": next_msg})
                            else:
                                st.session_state.theory_step = "github_questions"
                                st.session_state.q_attempts = 0 
                                st.session_state.chat_history.append({"role": "assistant", "content": "Excellent. We've completed the general theory part. Now, let's dive into the architecture of your GitHub repositories."})
                        elif st.session_state.theory_step == "github_questions":
                            st.session_state.github_q_idx += 1
                            st.session_state.q_attempts = 0

                            if st.session_state.github_q_idx < len(st.session_state.github_questions_list):
                                next_q = st.session_state.github_questions_list[st.session_state.github_q_idx]
                                next_msg = f"Alright, next repo question: {next_q['question_text']}"
                                st.session_state.chat_history.append({"role": "assistant", "content": next_msg})
                            else:
                                st.session_state.stage = "live_coding"
                                st.session_state.chat_history.append({"role": "assistant", "content": "Awesome! We've completed the architecture review. Get ready for some live coding."})
                st.rerun()
elif st.session_state.stage == "live_coding":
    st.header("Stage 3: Live Coding Sandbox")
    handler = LLMHandler()
    if "live_round" not in st.session_state:
        st.session_state.live_round = 1
        st.session_state.live_difficulty = "medium"
        st.session_state.live_scores = []   

    if "live_task" not in st.session_state:
        if "live_language" not in st.session_state:
            supported_langs = {"python", "java", "c++", "c"} 
            raw_candidate_langs = set() 

            if "tech_stack" in st.session_state:
                raw_candidate_langs.update([skill.lower() for skill in st.session_state.tech_stack])
            if "selected_repos" in st.session_state:
                raw_candidate_langs.update([r.get("language", "").lower() for r in st.session_state.selected_repos if r.get("language")])
            
            candidate_langs = set()
            for lang in raw_candidate_langs:
                if "python" in lang: candidate_langs.add("python")
                elif "java" in lang and "script" not in lang: candidate_langs.add("java")
                elif "c++" in lang or "cpp" in lang or "c/c++" in lang: candidate_langs.add("cpp")
                elif "c" == lang or "c/c++" in lang: candidate_langs.add("c")

            available_matches = list(candidate_langs.intersection(supported_langs))
            if not available_matches:
                st.warning("We couldn't automatically detect our supported live coding languages (Python, Java, C++, C) in your profile.")
                fallback_mapping = {"Python": "python", "Java": "java", "C++": "cpp", "C": "c"}
                selected_display = st.selectbox("Please select a language you are comfortable with:", list(fallback_mapping.keys()))

                if st.button("Confirm Language and Start"):
                    st.session_state.live_language = fallback_mapping[selected_display]
                    st.rerun()
                st.stop()
            else:
                import random
                st.session_state.live_language = random.choice(available_matches)
        with st.spinner(f"Preparing Round {st.session_state.live_round} environment..."):
            active_lang = st.session_state.live_language
            ui_lang = "C++" if active_lang == "cpp" else ("C" if active_lang == "c" else active_lang.capitalize())

            task = handler.fetch_live_coding_task(
                language=active_lang, 
                difficulty=st.session_state.live_difficulty
            )

            if task:
                st.session_state.live_task = task
                st.session_state.live_attempts = 0

                msg = f"Welcome to Round {st.session_state.live_round} of live coding. We are using {ui_lang}. Level: **{st.session_state.live_difficulty.upper()}**.\n\n**Task:**\n{task.get('task_description', 'ERROR.')}"
                if st.session_state.live_round == 1:
                    st.session_state.live_chat_history = [{"role": "assistant", "content": msg}]
                else:
                    st.session_state.live_chat_history.append({"role": "assistant", "content": msg})
            else:
                st.error("Live coding task could not be found! Moving to finish...")
                st.session_state.stage = "finished"
                st.rerun()

    if "live_task" in st.session_state:
        col1, col2 = st.columns([1, 1], gap="large")

        with col1:
            st.subheader(" Interviewer Chat")
            for message in st.session_state.live_chat_history:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])
        with col2:
            ui_lang_label = "C++" if st.session_state.live_language == "cpp" else ("C" if st.session_state.live_language == "c" else st.session_state.live_language.capitalize())
            st.subheader(f" Code Editor ({ui_lang_label})")
            st.info(" Write your code below. Use **Enter** for a new line. Click **'Run & Submit Code'** when you are ready.")

            ace_lang_map = {"python": "python", "java": "java", "cpp": "c_cpp", "c": "c_cpp"}
            mapped_lang = ace_lang_map.get(st.session_state.live_language)
            
            candidate_code = st_ace(
                language=mapped_lang,
                theme="monokai", 
                keybinding="vscode",
                font_size=15,
                tab_size=4,
                show_gutter=True,
                show_print_margin=False,
                wrap=True,
                auto_update=False, 
                key=f"ace_editor_r{st.session_state.live_round}"
            )
            
            if st.button("Run & Submit Code", type="primary", use_container_width=True):
                if not candidate_code.strip():
                    st.warning("Please write some code before submitting.")
                else:
                    st.session_state.live_attempts += 1
                    st.session_state.live_chat_history.append({"role": "user", "content": f"*(Submitted new code... Attempt {st.session_state.live_attempts})*"})

                    with st.spinner("Executing code in secure sandbox..."):
                        exec_logs = handler.execute_code_sandbox(candidate_code, language=st.session_state.live_language)
                        st.info(f"**Console Output:**\n```\nSTDOUT: {exec_logs.get('stdout', '')}\nSTDERR: {exec_logs.get('stderr', '')}\n```")
                    
                    with st.spinner("Interviewer is analyzing your code and execution logs..."):
                        history_str = ""
                        if st.session_state.live_attempts > 1:
                            relevant_history = st.session_state.live_chat_history[-3:-1]
                            history_str = "\n".join([f"{msg['role'].capitalize()}: {msg['content']}" for msg in relevant_history])

                        eval_res, usage = handler.evaluate_live_coding(
                            task_description=st.session_state.live_task.get("task_description", ""),
                            candidate_code=candidate_code,
                            execution_log=exec_logs,
                            language=st.session_state.live_language,
                            llm_model="llama-3.3-70b-versatile",
                            provider="groq",
                            conversation_history=history_str
                        )  

                        decision = eval_res.get("decision", "ERROR")
                        bot_speech = eval_res.get("bot_speech", "Sandbox evaluation failed. Let's try again.")
                        raw_score = eval_res.get("score", 5)

                        multipliers = {"easy": 1.0, "medium": 1.5, "hard": 2.0}
                        final_score = raw_score * multipliers.get(st.session_state.live_difficulty, 1.0)

                        if st.session_state.live_attempts == 1:
                            st.session_state.live_scores.append({
                                "round": st.session_state.live_round,
                                "difficulty": st.session_state.live_difficulty,
                                "scores_list": [final_score]
                            })
                        else:
                            current_idx = st.session_state.live_round - 1
                            if current_idx < len(st.session_state.live_scores):
                                st.session_state.live_scores[current_idx]["scores_list"].append(final_score)

                        round_finished = False
                        if decision == "PASS":
                            round_finished = True
                            if st.session_state.live_round == 1:
                                st.session_state.live_difficulty = "hard" 
                                bot_speech += " That was excellent. Let's step it up with a harder challenge."
                        elif st.session_state.live_attempts >= 3:
                            round_finished = True
                            decision = "PASS" 
                            if st.session_state.live_round == 1:
                                st.session_state.live_difficulty = "easy" 
                                bot_speech = "We are running out of time for this section. Let's pivot to a different fundamental problem."
                            else:
                                bot_speech = "Let's wrap up the coding execution here. You had some interesting approaches."
                            
                        st.session_state.live_chat_history.append({"role": "assistant", "content": bot_speech})

                        if round_finished:
                            if st.session_state.live_round == 1:
                                st.session_state.live_round = 2
                                del st.session_state.live_task 
                            else:
                                st.session_state.stage = "finished"
                                st.session_state.live_chat_history.append({"role": "assistant", "content": "Thank you! The technical assessment is now completely finished."})
                    
                        st.rerun()

elif st.session_state.stage == "finished":
    st.balloons()
    st.header(" Interview Completed!")
    st.success("You have successfully completed all stages of the AI Technical Interview.")

    db_final_scores = [sum(item["scores"]) / len(item["scores"]) for item in st.session_state.get("db_scores", [])]
    avg_db = sum(db_final_scores) / len(db_final_scores) if db_final_scores else 0

    gh_final_scores = [sum(item["scores"]) / len(item["scores"]) for item in st.session_state.get("github_scores", [])]
    avg_gh = sum(gh_final_scores) / len(gh_final_scores) if gh_final_scores else 0

    live_scores = st.session_state.get("live_scores", [])
    if live_scores:
        round_averages = [sum(item["scores_list"]) / len(item["scores_list"]) for item in live_scores]
        total_live_score = sum(round_averages)
        live_percentage = (total_live_score / 35.0) * 100
    else:
        live_percentage = 0
    
    st.markdown("###  Your Detailed Interview Report")
    col1, col2, col3 = st.columns(3)
    col1.metric("General Theory Score", f"{avg_db:.1f}/10")
    if st.session_state.get("github_flag", True): 
        col2.metric("GitHub Architecture Score", f"{avg_gh:.1f}/10")
    else:
        col2.metric("GitHub Architecture Score", "Skipped (N/A)")
    col3.metric("Live Coding Score", f"%{live_percentage:.1f}")

    st.markdown("---")
    st.subheader(" Backend API Token Usage Analysis")

    if "token_tracker" in st.session_state and st.session_state.token_tracker:
        total_prompt = sum(v["prompt"] for v in st.session_state.token_tracker.values())
        total_comp = sum(v["completion"] for v in st.session_state.token_tracker.values())
        total_all = sum(v["total"] for v in st.session_state.token_tracker.values())

        tc1, tc2, tc3 = st.columns(3)
        tc1.metric("Total Prompt Tokens", f"{total_prompt:,}")
        tc2.metric("Total Output Tokens", f"{total_comp:,}")
        tc3.metric("Total Consumed Tokens", f"{total_all:,}")
    
        name_map = {
            "parse_cv_with_llm_and_validation": " CV Parsing & Data Extraction",
            "generate_github_questions": " GitHub Architecture Question Generation",
            "evaluate_theory_answer": " General Theory Evaluation",
            "evaluate_github_answer": " GitHub Code Analysis Evaluation",
            "evaluate_live_coding": " Live Coding Evaluation"
        }

        st.markdown("#### Breakdowns by Module")
        for func_key, stats in st.session_state.token_tracker.items():
            if stats["total"] > 0:
                
                clean_name = name_map.get(func_key, func_key.replace("_", " ").title())
                
                st.caption(f"**{clean_name}**")
                st.text(f"Prompt: {stats['prompt']:,} | Completion: {stats['completion']:,} | Total: {stats['total']:,}")
    else:
        st.info("No token usage data recorded during this session.")