import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import pymysql
import yaml
import sqlalchemy

pymysql.install_as_MySQLdb()

with open(f'..\..\config.yaml', encoding='UTF-8') as f:
    _cfg = yaml.load(f, Loader=yaml.FullLoader)

# DB info
DB_SECRET = _cfg['DB_SECRET']
FS_DUMMY_TABLE = _cfg['TB_FS_DUMMY']  # test db table

def crawling_financial_statments(ticker: int):
    """
    crawling financial statements from https://finance.naver.com/
    :param ticker: ticker
    :return: no return
    """

    res = requests.get(f'https://finance.naver.com/item/coinfo.naver?code={ticker}')
    soup = BeautifulSoup(res.text, "lxml")

    stock_name = soup.select_one('.wrap_company > h2:nth-child(1) > a:nth-child(1)').text

    # iframe src
    referer = f'https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={ticker}'

    res = requests.get(referer)
    soup = BeautifulSoup(res.text, "lxml")

    # find href from iframe
    id_cnt = 0
    request_id = ''
    for i in soup.find('div', class_="all"):  # find all parents that have div.id
        try:
            # find id at 6th div
            if id_cnt < 6:
                request_id = i.attrs['id']
                # print("id_cnt : ", id_cnt, ", request_id:", request_id, i)
                id_cnt += 1
            else:
                break
        except Exception as e:
            # print(e)
            continue

    # print("request_id : ", request_id)

    javascript = soup.select_one('body > div > script')  # get javascript
    result = re.search("encparam", javascript.text)  # find encparam
    request_encparam = javascript.text[result.end() + 3:result.end() + 35]

    market = soup.select('dt.line-left')[8].text.split()[0]  # KOSPI or KOSDAQ
    sector = soup.select('dt.line-left')[8].text.split()[-1]  # industry classification(Main)
    industry = soup.select('dt.line-left')[9].text.split()[-1]  # industry classification(Sub)
    beta = soup.select_one(
        '#cTB11 > tbody > tr:nth-child(6) > td').text.lstrip().rstrip()  # beta, unique usage for this project

    # find cmp_cd(ticker) and encparam from javascript code(JSON type) because both params always changes
    # fin_typ=4 - IFRS linked financial statements only
    request_url = f"https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx?cmp_cd={ticker}&fin_typ=4&freq_typ=A&encparam={request_encparam}&id={request_id}"
    # print("request_url : ", request_url)

    # request headers to request with referer
    headers = {
        "Accept": "text/html, */*; q=0.01",
        "Accept-Encoding": "gzip, deflate, bcolumnsr",
        "Accept-Language": "ko,ko-KR;q=0.9,en-US;q=0.8,en;q=0.7",
        "Host": "navercomp.wisereport.co.kr",
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:107.0) Gecko/20100101 Firefox/107.0",
        "referer": referer,
    }

    res = requests.get(request_url, headers=headers)
    soup = BeautifulSoup(res.text, "lxml")

    # get published year of annual financial statements
    columns = []
    for i in soup.select('th[class^="r02c0"]')[:4]:
        column = i.text.lstrip().rstrip()[:7]
        columns.append(column)

    # get annual financial statements info
    financial_summary = soup.select('tbody')[1]

    # get index for Dataframe column
    index = []
    for i in financial_summary.select('th.bg.txt'):
        index.append(i.text.lstrip())

    df = pd.DataFrame(columns=columns, index=index)

    for idx, tr in enumerate(financial_summary.select('tr')):
        values = []
        # annual financial statement info only
        for td in tr.select('td')[:4]:
            try:
                value = td.select_one('span').text.replace(',', '')
            except Exception as e:
                value = 0
                # print(f'value error - select_one(span): ', e)
            values.append(value)

        # print("values : ", values)
        df.loc[index[idx]] = values

    df_T = df.T

    df_T['????????????'] = ticker
    df_T['??????'] = market
    df_T['?????????'] = stock_name
    df_T['?????????'] = sector
    df_T['?????????'] = industry
    df_T['????????????'] = beta
    df_T['????????????'] = 'n'  # Y - Close Biz / N - on going biz

    df_T = df_T.reset_index(drop=False)  # make published year as a column, not index
    df_T.rename(columns={'index': '?????????'}, inplace=True)

    # adjust columns for database table
    df_rename_columns = {'?????????': 'setting_date', '??????': 'market', '????????????': 'ticker', '?????????': 'stock_name',
                         '?????????': 'sector', '?????????': 'industry', '????????????': 'beta', '?????????': 'revenue',
                         '????????????': 'operating_profit', '????????????(????????????)': 'std_operating_profit',
                         '????????????????????????': 'continuing_operations_profit', '???????????????': 'net_income',
                         '???????????????(??????)': 'control_int_net_income', '???????????????(?????????)': 'uncontrol_int_net_income',
                         '????????????': 'total_asset', '????????????': 'total_debt', '????????????': 'total_capital',
                         '????????????(??????)': 'control_int_total_capital', '????????????(?????????)': 'uncontrol_int_total_capital',
                         '?????????': 'capital', '????????????????????????': 'cf_operation', '????????????????????????': 'cf_investing',
                         '????????????????????????': 'cf_financing', 'CAPEX': 'capex', 'FCF': 'fcf', '??????????????????': 'debt_from_int',
                         '???????????????': 'operating_margin', '????????????': 'net_margin', 'ROE(%)': 'roe', 'ROA(%)': 'roa',
                         '????????????': 'debt_ratio', '???????????????': 'retention_rate', 'EPS(???)': 'eps', 'PER(???)': 'per',
                         'BPS(???)': 'bps', 'PBR(???)': 'pbr', '??????DPS(???)': 'cash_dps', '?????????????????????': 'cash_div_return',
                         '??????????????????(%)': 'cash_div_payout_ratio', '???????????????(?????????)': 'issued_shares', '????????????': 'closure_yn'
                         }

    df_T.rename(columns=df_rename_columns, inplace=True)

    # DB insert
    try:
        engine = sqlalchemy.create_engine(f'mysql://root:{DB_SECRET}@localhost:3306/sqldb', encoding='utf8')
        df_T.to_sql(name=FS_DUMMY_TABLE, con=engine, if_exists='append', index=False)
        print(f"{stock_name}'s financial statement info insert into {FS_DUMMY_TABLE}")
    except Exception as e:
        print(f'DB insert exception({FS_DUMMY_TABLE}): ', e)

# test
crawling_financial_statments('214870')
