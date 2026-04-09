import fitz
import json
import os
import sys
import traceback
from llm_handler import LLMHandler
from pydantic import BaseModel, Field, ValidationError
from dotenv import load_dotenv
from typing import List, Optional
import re
import requests
import base64
import time
import ast

load_dotenv()

##Pydantic
#class Education(BaseModel):
#    university: Optional[str] = Field(default="Not Specified", description="Exact name of the university or institution.")
#    degree: Optional[str] = Field(default="Not Specified", description="Degree, major, or field of study.")
#    start_date: Optional[str] = Field(default="Not Specified", description="Start date (e.g., MM/YYYY).")
#    end_date: Optional[str] = Field(default="Present", description="End date (e.g., MM/YYYY) or 'Present' if ongoing.")
#    gpa: Optional[str] = Field(default="Not Specified", description="Grade Point Average or final grade, if explicitly stated.")

class Project(BaseModel):
    name: str = Field(..., description="Name of the project. This field is mandatory.")
    tech_stack: Optional[str] = Field(default="Not Specified", description="Technologies, programming languages, or tools used in the project.")
    description: str = Field(..., description="Explanation of the project's architecture and the candidate's specific contributions. You MUST use 'description' as the JSON key.")

class TechSkillCategory(BaseModel):
    category: Optional[str] = Field(default="General Skills", description="Category name (e.g., 'Programming Languages', 'Core Engineering').")
    skills: List[str] = Field(default_factory=list, description="List of specific technical skills belonging to this category.")

class Links(BaseModel):
    name: Optional[str] = Field(
        default="Not Specified", description="A short descriptive label for the link. Example: 'GitHub', 'LinkedIn', 'Personal Portfolio', 'Email'. Infer the name from the domain of the URL."
    )
    link: Optional[str] = Field(
        default="Not Specified", description="The actual URL string."
    )

class CVProfile(BaseModel):
    #iscv eklemeye calis
#    education: List[Education] = Field(default_factory=list, description="List of educational backgrounds.")
    experience_titles: List[str] = Field(default_factory=list, description="List of job titles or roles the candidate has held.")
    projects: List[Project] = Field(default_factory=list, description="List of technical projects the candidate has worked on.")
    technical_skills: List[TechSkillCategory] = Field(default_factory=list, description="Categorized list of technical skills.")
    links: List[Links] = Field(default_factory = list, description="List of links")

def extract_text_from_pdf(pdf_path: str) -> str:
    if not os.path.exists(pdf_path):
        print(f"Document Error: '{pdf_path} doesn´t exists")
        return None
    
    text = ""
    found_urls = set()
    url_pattern = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+')

    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            text += page.get_text()

            for match in url_pattern.findall(text):
                found_urls.add(match.strip())
            
            links = page.get_links()
            for link_item in links:
                if 'uri' in link_item:
                    found_urls.add(link_item['uri']) 
        doc.close()
        
        if found_urls:
            text += "\n\n--- Validated Links Extracted By System ---\n"
            for url in found_urls:
                clean_url = url.strip('.,')
                text += f"Link Item: {clean_url}\n"
        return text
    
    except Exception as e:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        formatted_traceback = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        
        error_details = (
            f"---- Unexpected Error ----"
            f"type: {exc_type.__name__}"
            f"Msg: {str(e)}\n"
            f"Traceback: \n {formatted_traceback}"
        )
        print(error_details)
        return None

def analyze_github_links(cv_data: dict):
    found_repos = []
    profile_username = None

    repo_pattern = re.compile(r"github\.com/([^/?#]+)/([^/?#]+)")
    profile_pattern = re.compile(r"github\.com/([^/?#]+)")

    for item in cv_data.get("links", []):
        url = item.get("link", "")
        if "github.com" in url.lower():
            repo_match = repo_pattern.search(url)

            if repo_match:
                username = repo_match.group(1)
                repo_name = repo_match.group(2)
                print(f"Directly pulled Repo Link: {repo_name} (Username: {username})")
                found_repos.append({"username": username, "repo": repo_name})
                continue
            
            profile_match = profile_pattern.search(url)
            if profile_match:
                profile_username = profile_match.group(1)
                print(f"Only profile link founded: {profile_username}")

    return found_repos, profile_username

def fetch_user_repos(username: str):
    print(f"{username} repos are being pulled!")
    api_url = f"https://api.github.com/users/{username}/repos"
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        repos = response.json()

        return [
            {
                "name": r["name"],
                "desc": r["description"],
                "language": r["language"],
                "url": r["html_url"],
                "default_branch": r.get("default_branch", "main")
            }
            for r in repos
        ]
    except Exception as e:
        print(f"GitHub API Error: {e}")
        return None

def clean_and_tokenize(text: str):
    if not text:
        return set()
    
    words = re.findall(r'[a-z0-9]+', text.lower())
    stopwords = {'project', 'app', 'application', 'system', 'wrapper', 'advanced', 'management', 'api', 'web', 'tool', 'my', 'the', 'a', 'an', 'and', 'or', 'on', 'in', 'of', 'for', 'to'}
    return set([w for w in words if w not in stopwords])

def match_repos_locally(cv_projects: list, all_repos: list):
    matched_targets = []

    for proj in cv_projects:
        proj_name = proj.get("name", '') if isinstance(proj, dict) else getattr(proj, 'name', '')

        cv_tokens = clean_and_tokenize(proj_name)
        best_match = None
        highest_score = 0

        for repo in all_repos:
            repo_text = f"{repo['name']} {repo['desc'] or ''}"
            repo_tokens = clean_and_tokenize(repo_text)

            score = len(cv_tokens.intersection(repo_tokens))
            if score > highest_score:
                highest_score = score
                best_match = repo
        if highest_score >= 1 and best_match:
            print(f"Match: CV['{proj_name}'] -> GitHub['{best_match['name']}'] (Score: {highest_score})")
            matched_targets.append({
                "cv_project": proj_name,
                "username": best_match['url'].split('/')[-2],
                "repo": best_match['name'],
                "def_branch": best_match.get('default_branch'),
                "language": best_match.get("language")
            })
    return matched_targets

def get_single_repo_info(username: str, repo_name: str) -> str:
    api_url = f"https://api.github.com/repos/{username}/{repo_name}"
    try:
        response = requests.get(api_url, timeout = 5)
        if response.status_code == 200:
            data = response.json()
            return {
                "default_branch": data.get("default_branch", "main"),
                "language": data.get("language")
            }
        else:
            return None
    except Exception as e:
        print(f"Error during learning Branch: {e}")
        return None

def analyze_code_skeleton(file_name: str, code_content: str):
    skeleton = []
    if file_name.endswith('.py'):
        try:
            tree = ast.parse(code_content)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    skeleton.append(f"Class: {node.name}")
                elif isinstance(node, ast.FunctionDef):
                    args = [arg.arg for arg in node.args.args]
                    args_str = ", ".join(args)
                    skeleton.append(f"Function: {node.name}({args_str})")
        except Exception:
            pass

    elif file_name.endswith(('.java', '.cpp', '.ino')):
        class_pattern = r'(?:public\s+|private\s+|protected\s+)?(?:abstract\s+)?class\s+(\w+)'
        for c in re.findall(class_pattern, code_content):
            skeleton.append(f"Class: {c}")
        
        method_pattern = r'(?:public|private|protected)?\s*(?:static\s+)?[\w\<\>\[\]]+\s+(\w+)\s*\(([^\)]*)\)\s*(?:throws\s+[\w,\s]+)?\s*\{'
        for m in re.findall(method_pattern, code_content):
            if m not in ('if', 'for', 'while', 'switch', 'catch', 'else'):
                skeleton.append(f"Method: {m}()")
                
    return list(set(skeleton))    



def fetch_repo_contents_smart(username: str, repo_name: str, branch: str):
    print(f"{repo_name} all folders are being scanned...")
    base_url = f"https://api.github.com/repos/{username}/{repo_name}"
    repo_data = {
        "readme": "",
        "dependencies": {},
        "source_codes": {}
    }

    tree_url = f"{base_url}/git/trees/{branch}?recursive=1"
    tree_resp = requests.get(tree_url)

    if tree_resp.status_code != 200:
        print("Can´t access to Repo content tree. Status: {tree_resp.status_code}")
        return repo_data

    try:
        readme_resp = requests.get(f"{base_url}/readme")
        if readme_resp.status_code == 200:
            readme_json = readme_resp.json()
            raw_readme = base64.b64decode(readme_json['content']).decode('utf-8')
            repo_data["readme"] = _clean_readme(raw_readme)
            
    except Exception as e:
        print(f"Readme pull error: {e}")

    tree = tree_resp.json().get('tree', [])
    target_extensions = ('.py', '.java', '.cpp', '.ino')
    target_dependencies = ('pom.xml', 'requirements.txt', 'package.json')

    for item in tree:
        if item['type'] == 'blob':
            file_path = item['path']
            file_name = file_path.split('/')[-1]

            raw_url = f"https://raw.githubusercontent.com/{username}/{repo_name}/{branch}/{file_path}"
            try:
                if file_name in target_dependencies:
                    resp = requests.get(raw_url, timeout = 6)
                    if resp.status_code == 200:
                        repo_data["dependencies"][file_path] = resp.text               
                    time.sleep(0.1)

                elif file_name.endswith(target_extensions):
                    resp = requests.get(raw_url, timeout = 6)
                    if resp.status_code == 200:
                        raw_code = resp.text
                        skeleton = analyze_code_skeleton(file_name, raw_code)
                        if skeleton:
                            repo_data["source_codes"][file_path] = skeleton
                    time.sleep(0.1)
            except Exception as e:
                print(f"{file_name} skipped because of network error: {e}")

    return repo_data


def github_data_engineering_pipeline(cv_data: dict):
    found_repos, profile_username = analyze_github_links(cv_data)
    target_repos = []
    added_repo_ids = set()

    if profile_username:
        print(f"Profile founded: {profile_username}. Repos are going to be pulled from the API...")
        all_repos = fetch_user_repos(profile_username)
        if all_repos:
            print("Local text matching process started...")
            cv_projects = cv_data.get("projects", [])
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
                default_branch = get_default_branch_for_single_repo(repo['username'], repo['repo'])
                target_repos.append({
                    "username": repo["username"],
                    "repo": repo["repo"],
                    "def_branch": default_branch,
                    "cv_project": repo["repo"]
                })
                added_repo_ids.add(unique_id)
    
    if not target_repos:
        print("No Valid GitHub link founded. (UIdan manuel iste)")
        return None
    
    print("\n---- Pulling Repo Contents ----")
    final_pipeline_data = []

    for repo_info in target_repos:
        username = repo_info["username"]
        repo_name = repo_info["repo"]
        branch = repo_info["def_branch"]

        repo_details = fetch_repo_contents_smart(username, repo_name, branch)
        repo_packet = {
            "cv_project_name": repo_info.get("cv_project"),
            "username": username,
            "repo_name": repo_name,
            "branch": branch,
            "fetched_data": repo_details
        }

        final_pipeline_data.append(repo_packet)

    return final_pipeline_data

def _clean_readme(raw_readme: str) -> str:
    garbage_headers = ["license", "contrinuting", "installation", "setup", "run locally", "sponsors", "changelog", "deploy"]
    sections = re.split(r'(?m)^#{1,6}\s+(.*)', raw_readme)
    if not sections or len(sections) == 1:
            return raw_readme
    cleaned_readme = sections[0]
    for i in range(1, len(sections), 2):
            header = sections[i]
            content = sections[i+1] if i+1 < len(sections) else ""
            
            if not any(garbage in header.lower() for garbage in garbage_headers):
                cleaned_readme += f"\n## {header}\n{content}"
                
    return cleaned_readme.strip()