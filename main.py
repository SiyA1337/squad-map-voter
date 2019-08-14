import sys
import random
import threading
from collections import Counter
import logging
import re
import configparser
from ServerCommands import ServerCommands
import glob
import os
import datetime

class MapVoter:

    config = configparser.ConfigParser()
    map_candidates = {}
    votes = {}
    voting_active = False

    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d-%H%M")

    logging.basicConfig(level=logging.DEBUG, filename=f'./logs/mapvote-{timestamp}.log', filemode='a', format='%(asctime)s - %(levelname)s - %(message)s')

    def __init__(self):

        if not len(sys.argv) == 2:
            print('Usage: python3 main.py <path to config file>')
            return

        config_path = sys.argv[1]
        self.config.read(f'{config_path}')

        try:
            if len(self.config['MapVoter']) <= 0:
                print('Configuration file not loaded properly.')
                return
        except:
            print('Error loading configuration file.')
            return

        self.server = ServerCommands(self.config)


        logging.info('Squad Map Voter initialized')

        # start a separate thread for the server log listener
        sl = threading.Thread(target=self.start_read_server_logs, args=())
        sl.start()

        # start another separate thread for the chat log listener
        cl = threading.Thread(target=self.start_read_chat_logs, args=())
        cl.start()

    def start_vote_delay(self):
        time = self.config['MapVoter'].getfloat("vote_delay")
        vote_delay_timer = threading.Timer(time, self.start_vote)
        vote_delay_timer.start()
        logging.info('Voting will begin in %f seconds.', time)

    def start_vote(self):
        if self.voting_active:
            logging.error('Voting cannot be started while a vote is still ongoing.')
            return

        self.map_candidates = self.get_map_candidates()

        candidates_string = ""
        for key in self.map_candidates:
            candidates_string += f"{key}. {self.map_candidates[key]} \n"

        logging.info('Map candidates are %s', candidates_string)

        self.server.broadcast(f"Map voting has begun! Type !vote followed by a number to vote.\n{candidates_string}\nExample: !vote 1")

        time = self.config['MapVoter'].getfloat("vote_duration")

        # start vote timer
        vote_timer = threading.Timer(time, self.end_vote)
        vote_timer.start()

        self.voting_active = True

        logging.info('Voting has been started and will end in %f seconds.', time)

    def end_vote(self):
        self.voting_active = False
        logging.info('Voting has ended.')

        winning_map = self.get_winning_map()
        if not winning_map:
            return

        # broadcast winning map
        self.server.broadcast(f"Voting has ended. {winning_map[0]} has won with {winning_map[1]} votes!")

        #set next map to winning map
        self.server.set_map(winning_map[0])

    def detect_match_start(self, log_line):
        match = re.search(r"LogWorld: SeamlessTravel to:", log_line)
        if match:
            self.start_vote_delay()

    def detect_user_vote(self, log_line):
        match = re.search(r"!vote", log_line)
        if match:
            if self.voting_active:
                # strip whitespace in log line and separate with commas
                # format: 0:time, 1:chat_type, 2:user_name, 3:message
                vals = log_line.split("    ") #data separated with 4 spaces
                voter_id = vals[2]
                command_index = vals[3].find('!vote')
                # get the char immediately after !vote
                vote_choice = vals[3][command_index+5:command_index+7].strip()
                # only continue if the vote value is a positive integer
                try:
                    self.store_vote(voter_id, int(vote_choice))
                except:
                    logging.info('User %s has submitted an invalid vote value (%s).', voter_id, vote_choice)
            else:
                logging.debug('A vote has been detected outside of the voting period: %s', log_line)

    def detect_vote_initiate(self, log_line):
        match = re.search(r"!mapvote", log_line)
        if match:
            if not self.voting_active:
                # strip whitespace in log line and separate with commas
                # format: 0:time, 1:chat_type, 2:user_name, 3:message
                vals = log_line.split("    ") #data separated with 4 spaces
                sender_id = vals[2]
                chat_type = vals[1]
                if chat_type == 'ChatAdmin':
                    logging.info('A map vote was manually initiated by: %s', vals[2])
                    self.start_vote()
                else:
                    logging.info('An attempt was made to initiate a vote outside of AdminChat: %s', log_line)
            else:
                logging.info('An attempt was made to initiate a vote while one is already running: %s', log_line)

    def start_read_server_logs(self):
        try:
            server_log = open(self.config['MapVoter']['server_log_path'], 'r')
            server_log.seek(0, 2)
            while True:
                line = server_log.readline()
                if line != "\n" and line != "":
                    self.detect_match_start(line)
        except:
            logging.error('Error loading server log file!', exc_info=True)
        finally:
            server_log.close()

    def start_read_chat_logs(self):
        chat_log_path = self.config['MapVoter']['chat_log_path']
        all_log_files = glob.glob(f'{chat_log_path}/*')
        latest_log = max(all_log_files, key=os.path.getmtime)

        try:
            chat_log = open(latest_log, 'r')
            chat_log.seek(0, 2)
            while True:
                line = chat_log.readline()
                if line != "\n" and line != "":
                    self.detect_user_vote(line)
                    self.detect_vote_initiate(line)
        except:
            logging.error('Error loading chat log file!', exc_info=True)
        finally:
            chat_log.close()


    def store_vote(self, voter_id, vote_choice):
        if vote_choice not in self.map_candidates.keys():
            logging.info('User %s has submitted an invalid vote (%i).', voter_id, vote_choice)
            return False
        else:
            self.votes.update({voter_id:vote_choice})
            logging.info('User %s has voted for option %i', voter_id, vote_choice)
            return True

    def get_winning_map(self):
        options = []

        if len(self.votes) <= 0:
            logging.info('Voting has ended. No votes were cast!')
            self.server.broadcast(f"Voting has ended. No votes were cast!")
            return False

        for key in self.votes:
            options.append(self.votes[key])

        votes_count = Counter(options)
        winning_value = votes_count.most_common(1)[0]

        if not winning_value:
            logging.error('Problem in calculating the winning map!', exc_info=True)
            return False

        winning_map_id = winning_value[0]
        winning_map_votes = winning_value[1]
        winning_map = self.map_candidates.get(winning_map_id)
        logging.info('Winning map is %s with %i / %i votes.', winning_map, winning_map_votes, len(self.votes))

        return [winning_map, winning_map_votes]

    def get_map_list(self):
        map_list = open(self.config['MapVoter']['map_rotation_path'], 'r')
        maps = []
        try:
            if map_list.mode == "r":
                line = map_list.readline()
                while line:
                    if line != "\n":
                        maps.append(line)
                    line = map_list.readline()
        except:
            logging.error('Failed to load Map Rotation file.')
        finally:
            map_list.close()
        return maps

    def get_map_candidates(self):
        map_list = self.get_map_list()
        candidates = {}
        for i in range(self.config['MapVoter'].getint('num_map_candidates')):
            candidates.update({i+1:map_list[random.randint(0,len(map_list))].rstrip()})
        return candidates

if __name__ == "__main__":
    v = MapVoter()
