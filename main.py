import os
import re
import time
import json
import sqlite3

import requests
from markdownify import markdownify as md

fenbi_cookies_raw = ''
fake_ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.106 Safari/537.36'


class Database:
    db_file = r'mistake.sqlite'

    def __init__(self):
        self.init_db()
        self.db = sqlite3.connect(self.db_file)

    def init_db(self):
        if not os.path.exists(self.db_file):
            open(self.db_file, 'a').close()  # Touch Database File

            db = sqlite3.connect(self.db_file)
            db.execute('create table config ( name text primary key, value text);')
            db.execute('create table questions (id int primary key, data text);')
            db.commit()
            db.close()

    def exec(self, sql='', param=None):
        cur = self.db.cursor()
        cur.execute(sql, param)
        self.db.commit()
        return cur

    def get_config(self, key):
        cur = self.exec('SELECT `value` FROM `config` WHERE `name` = ?', [key])
        d = cur.fetchone()
        return d[0] if d else False

    def set_config(self, key, value):
        old_config = self.get_config(key)
        if old_config:  # UPDATE
            self.exec("UPDATE `config` SET `value` = ? WHERE `name` = ?", [value, key])
        else:  # INSERT
            self.exec('INSERT INTO `config` (`name`,`value`) VALUES (?, ?)', [key, value])

    def new_questions(self, qid, data):
        self.exec('INSERT INTO `questions` (`id`, `data`) VALUES (?, ?)', [qid, json.dumps(data, ensure_ascii=False)])


def cookies_raw2jar(raw: str) -> dict:
    """
    Arrange Cookies from raw using SimpleCookies
    """
    if not raw:
        raise ValueError("The Cookies is not allowed to be empty.")

    from http.cookies import SimpleCookie
    cookie = SimpleCookie(raw)
    return {key: morsel.value for key, morsel in cookie.items()}


def get_questions_from_keypoint(keypoint: dict) -> set:
    questions = []
    for i in keypoint:
        questions.extend(i.get('questionIds', []))
    return set(questions)


def list_chunk(input: list, size: int) -> list:
    return [input[i: i + size] for i in range(0, len(input), size)]


def clean_md(string: str) -> str:
    return re.sub('\n{2,}', '\n', md(string)).strip()


answer_map = {'0': 'A', '1': 'B', '2': 'C', '3': "D"}

if __name__ == '__main__':
    db = Database()
    s = requests.Session()

    s.headers.update({
        'User-Agent': fake_ua
    })
    s.cookies.update(cookies_raw2jar(fenbi_cookies_raw))

    old_keypoint_raw = db.get_config('keypoint-tree') or '[]'
    old_keypoint = json.loads(old_keypoint_raw)

    # 获取历史问题
    old_questions = get_questions_from_keypoint(old_keypoint)

    # 获得新的问题（全体）
    keypoint_req = s.get('https://tiku.fenbi.com/api/xingce/errors/keypoint-tree')
    db.set_config('keypoint-tree', keypoint_req.text)
    keypoint = keypoint_req.json()
    full_questions = get_questions_from_keypoint(keypoint)

    # 集合求差
    diff_questions = list(full_questions.difference(old_questions))
    diff_questions_chunk = list_chunk(diff_questions, 15)

    # 获取所有新问题详情
    new_solutions = []
    for question_chunk in diff_questions_chunk:
        s_r = s.get('https://tiku.fenbi.com/api/xingce/solutions',  # 从solutions中拿数据，因为更全。。。
                    params={'ids': ','.join(map(lambda x: str(x), question_chunk))})
        s_rj = s_r.json()
        new_solutions.extend(s_rj)

        for solution in s_rj:
            qid = solution.get('id')
            db.new_questions(qid, solution)

    new_question_md = ''
    new_answer_md = ''
    for i, q in enumerate(new_solutions):
        new_question_md += '{}. ({}, {})   \n'.format(  # 题号. (考点， 来源) 难度系数： 系数
            i + 1, '/'.join(map(lambda x: x['name'], q['keypoints'])),
            q.get('source'))

        # 处理材料（如果有的话）
        if q['material']:
            new_question_md += clean_md(q['material']['content'])

        # 处理题干
        new_question_md += clean_md(q['content']) + '  \n'

        # 处理选项
        new_question_md += " A. {}  \n B. {}  \n C. {}  \n D. {}  \n".format(
            clean_md(q['accessories'][0]['options'][0]),
            clean_md(q['accessories'][0]['options'][1]),
            clean_md(q['accessories'][0]['options'][2]),
            clean_md(q['accessories'][0]['options'][3]),
        )

        # 处理解答
        new_answer_md += '{}. 正确答案： {} （正确率： {:.0%}， 易错项 {}，难度系数： {}）  \n{}'.format(
            i + 1,
            answer_map[q['correctAnswer']['choice']],
            q['questionMeta']['correctRatio'] / 100,
            answer_map[q['questionMeta']['mostWrongAnswer']['choice']],
            q['difficulty'],
            clean_md(q['solution']),
        ).strip()

        new_question_md += '\n\n'
        new_answer_md += '\n\n'

    with open('output_{}.md'.format(time.time()), 'w', encoding='utf-8') as f:
        f.write(new_question_md + '\n\n\n\n' + new_answer_md)
