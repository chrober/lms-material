/**
 * LMS-Material
 *
 * Copyright (c) 2018-2024 Craig Drummond <craig.p.drummond@gmail.com>
 * MIT license.
 */
'use strict';

Vue.component('lms-dstm-dialog', {
    template: `
<v-dialog v-model="show" v-if="show" persistent width="450" class="lms-dialog">
 <v-card v-clickoutside="outsideClick">
  <v-card-text>
   <v-container grid-list-md style="padding: 4px">
    <v-layout wrap>
     <v-flex xs12 class="dlgtitle">{{i18n("Don't Stop The Music")}}</v-flex>
     <v-flex xs12>
      <v-list class="sleep-list dialog-main-list">
       <template v-for="(item, index) in items">
        <v-list-tile @click="setDstm(item.key)">
         <div :tile="true" v-if="boundKeys" class="choice-key">{{9==index ? 0 : index+1}}</div>
         <v-list-tile-avatar><v-icon small>{{item.selected ? 'radio_button_checked' :'radio_button_unchecked'}}</v-icon></v-list-tile-avatar>
         <v-list-tile-content>{{item.label}}</v-list-tile-content>
        </v-list-tile>
        <v-divider></v-divider>
       </template>
      </v-list>
     </v-flex>
    </v-layout>
   </v-container>
  </v-card-text>
  <v-card-actions>
   <v-spacer></v-spacer>
   <v-btn flat @click.native="cancel()">{{i18n('Cancel')}}</v-btn>
  </v-card-actions>
 </v-card>
</v-dialog>
`,
    props: [],
    data() {
        return {
            show: false,
            items:[],
            boundKeys: false
        }
    },
    mounted() {
        bus.$on('dstm.open', function() {
            this.boundKeys = false;
            if (LMS_P_DSTM) {
                lmsCommand(this.$store.state.player.id, ["dontstopthemusicsetting"]).then(({data}) => {
                    if (data.result && data.result.item_loop) {
                        this.items=[];
                        for (let i=0, loop=data.result.item_loop, len=loop.length; i<len; ++i) {
                            if (loop[i].actions && loop[i].actions.do && loop[i].actions.do.cmd) {
                                this.items.push({key: loop[i].actions.do.cmd[2], label:loop[i].text, selected:1===loop[i].radio});
                            }
                        }
                        bindNumeric(this);
                        this.show=true;
                    }
                });
            }
        }.bind(this));
        bus.$on('noPlayers', function() {
            this.cancel();
        }.bind(this));
        bus.$on('closeDialog', function(dlg) {
            if (dlg == 'dstm') {
                this.cancel();
            }
        }.bind(this));
        handleNumeric(this, this.setDstm, 'key');
    },
    methods: {
        outsideClick() {
            setTimeout(function () { this.cancel(); }.bind(this), 50);
        },
        cancel() {
            this.show=false;
            unbindNumeric(this);
        },
        setDstm(value) {
            bus.$emit("dstm", this.$store.state.player.id, value);
            this.show=false;
            unbindNumeric(this);
        },
        i18n(str, arg) {
            if (this.show) {
                return i18n(str, arg);
            } else {
                return str;
            }
        }
    },
    watch: {
        'show': function(val) {
            this.$store.commit('dialogOpen', {name:'dstm', shown:val});
        }
    }
})

